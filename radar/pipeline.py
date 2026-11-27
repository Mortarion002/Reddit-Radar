"""The shared processing pipeline used by `import`, `scan` and `ingest-eml`.

resend failures -> store + score -> hot alerts (thread rule) -> digest -> export
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping

from .config import Settings
from .digest import RunSummary, format_alert, format_digest, status_buttons
from .export import export_leads
from .models import Lead, Mention
from .scorer import ScoringConfig, build_lead
from .store import Store
from .telegram import TelegramClient

log = logging.getLogger("radar.pipeline")

THREAD_WINDOW_HOURS = 24


@dataclass
class Context:
    settings: Settings
    store: Store
    telegram: TelegramClient
    now: datetime
    dry_run: bool = False

    @property
    def cfg(self) -> ScoringConfig:
        return ScoringConfig.from_settings(self.settings)


def resend_pending(ctx: Context) -> tuple[int, int]:
    """Resend everything waiting in data/delivery_failures/ (alerts, digests, drafts, warnings).

    Every command that sends to Telegram calls this first, so nothing undelivered waits longer
    than the next run of any of them. A resent draft gets its Telegram message id recorded, so
    its buttons keep working for the mention ratio.
    """

    def on_sent(meta: dict, message_id: int) -> None:
        draft_id = meta.get("draft_id")
        if isinstance(draft_id, int):
            ctx.store.mark_draft_delivered(draft_id, message_id or None, ctx.now)

    return ctx.telegram.resend_failures(on_sent=on_sent)


def store_and_score(ctx: Context, mentions: list[Mention], email_uids: Mapping[str, str] | None = None) -> list[Lead]:
    new = ctx.store.upsert_mentions(mentions, email_uids=email_uids, now=ctx.now)
    cfg = ctx.cfg
    leads = [build_lead(m, cfg, ctx.now) for m in new]
    for lead in leads:
        ctx.store.save_lead(lead)
    return leads


def send_hot_alerts(ctx: Context, leads: list[Lead]) -> tuple[int, list[str]]:
    """Alert each new High lead once. Returns (alerts sent, mention_ids diverted to the digest)."""
    sent, diverted = 0, []
    since = ctx.now - timedelta(hours=THREAD_WINDOW_HOURS)
    for lead in sorted((l for l in leads if l.tier == "High"), key=lambda l: -l.score):
        current = ctx.store.get_lead(lead.mention_id)
        if current is None or current.alerted_at is not None:
            continue
        if ctx.store.thread_alerted_since(lead.mention.post_id, since, exclude=lead.mention_id):
            log.info("Thread %s already alerted in the last 24h; %s goes to the digest", lead.mention.post_id, lead.mention_id)
            diverted.append(lead.mention_id)
            continue
        ctx.telegram.send(format_alert(lead, ctx.now), status_buttons(lead.short_id), kind="alert")
        # Marked even if delivery failed: the message is preserved and resent, so it must not be re-alerted.
        ctx.store.mark("alerted_at", [lead.mention_id], ctx.now)
        sent += 1
    return sent, diverted


def send_digest(ctx: Context, summary: RunSummary, new_leads: list[Lead]) -> bool:
    """Send the digest when there is something new. Lists undigested leads that weren't hot-alerted."""
    pending = ctx.store.undigested_leads(ctx.now - timedelta(hours=ctx.settings.max_lead_age_hours))
    listed = [l for l in pending if l.alerted_at is None]
    # Skipped F5Bot hits also trigger a digest, so they show up on Telegram, not only in the log.
    has_new = any(l.tier in ("High", "Medium") for l in new_leads) or summary.skipped_hits > 0
    if not listed and not has_new:
        return False
    text = format_digest(summary, listed, ctx.now, ctx.settings.zoneinfo())
    ctx.telegram.send(text, kind="digest", silent=True)
    ctx.store.mark("digested_at", [l.mention_id for l in pending], ctx.now)
    return True


def process(
    ctx: Context,
    mentions: list[Mention],
    summary: RunSummary | None = None,
    email_uids: Mapping[str, str] | None = None,
) -> RunSummary:
    summary = summary or RunSummary()
    if not summary.mentions_found:
        summary.mentions_found = len(mentions)
    resend_pending(ctx)

    leads = store_and_score(ctx, mentions, email_uids)
    summary.new_mentions = len(leads)
    for lead in leads:
        summary.tiers[lead.tier] = summary.tiers.get(lead.tier, 0) + 1

    summary.alerts_sent, _ = send_hot_alerts(ctx, leads)
    send_digest(ctx, summary, leads)
    try:
        export_leads(ctx.store, ctx.settings, ctx.now, dry_run=ctx.dry_run)
    except OSError as exc:
        log.error("Leads export failed: %s", exc)
        summary.errors.append(f"export: {exc}")
    return summary
