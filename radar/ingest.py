"""`scan` (live IMAP) and `ingest-eml` (saved .eml files): emails -> mentions -> pipeline."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .digest import RunSummary
from .models import Mention
from .pipeline import Context, process
from .sources.f5bot_mail import Mailbox, MailboxError
from .sources.f5bot_parser import ParseError, message_id, parse_email_result, parse_message, received_at
from .store import Store, iso, parse_dt

log = logging.getLogger("radar.ingest")

WARNING_INTERVAL_HOURS = 6
WARNING_STATE_KEY = "imap_warning_at"


@dataclass
class EmailRecord:
    uid: str
    message_id: str
    received_at: datetime | None
    mentions_found: int
    status: str  # ok | partial (some hits skipped) | no_reddit | parse_error


@dataclass
class ParsedBatch:
    mentions: list[Mention] = field(default_factory=list)
    uid_by_mention: dict[str, str] = field(default_factory=dict)
    records: list[EmailRecord] = field(default_factory=list)

    @property
    def ok_uids(self) -> list[str]:
        return [r.uid for r in self.records if r.status != "parse_error"]


def _safe_name(uid: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", uid).strip("_") or "email"


def save_unparsed(settings: Settings, uid: str, raw: bytes) -> Path:
    folder = settings.unparsed_dir
    folder.mkdir(parents=True, exist_ok=True)
    name = _safe_name(uid)
    path = folder / (name if name.endswith(".eml") else f"{name}.eml")
    path.write_bytes(raw)
    return path


def _email_date(raw: bytes) -> datetime | None:
    try:
        return received_at(parse_message(raw))
    except Exception:
        return None


def parse_batch(settings: Settings, emails: list[tuple[str, bytes]], dry_run: bool, summary: RunSummary) -> ParsedBatch:
    """Parse each email. A bad email is saved to data/unparsed/ and the batch carries on."""
    batch = ParsedBatch()
    for uid, raw in emails:
        mid = message_id(raw)
        try:
            result = parse_email_result(raw)
        except Exception as exc:  # ParseError, or any parser bug: never lose the run
            summary.parse_errors += 1
            summary.skipped_hits += len(getattr(exc, "failed_hits", []) or [])
            reason = str(exc) if isinstance(exc, ParseError) else f"{type(exc).__name__}: {exc}"
            summary.errors.append(f"parse {uid}: {reason}")
            if dry_run:
                log.warning("Email %s did not parse (%s) [dry-run: not saved]", uid, reason)
            else:
                path = save_unparsed(settings, uid, raw)
                log.warning("Email %s did not parse (%s); saved to %s", uid, reason, path)
            batch.records.append(EmailRecord(uid, mid, _email_date(raw), 0, "parse_error"))
            continue
        mentions = result.mentions
        for m in mentions:
            batch.uid_by_mention.setdefault(m.mention_id, uid)
        batch.mentions.extend(mentions)
        status = "ok" if mentions else "no_reddit"
        if result.failed_hits:
            # Some hits in this email didn't parse: count them, log each URL, and keep a copy
            # of the email in data/unparsed/ so the hits can be checked by hand.
            status = "partial"
            summary.skipped_hits += len(result.failed_hits)
            for url in result.failed_hits:
                summary.errors.append(f"skipped hit in {uid}: {url}")
            if not dry_run:
                path = save_unparsed(settings, uid, raw)
                log.warning("Email %s: %d hit(s) skipped; copy saved to %s", uid, len(result.failed_hits), path)
        batch.records.append(EmailRecord(uid, mid, _email_date(raw), len(mentions), status))
        log.info("Email %s: %d Reddit mention(s)", uid, len(mentions))
    summary.emails_read += len(emails)
    summary.mentions_found += len(batch.mentions)
    return batch


def record_batch(store: Store, batch: ParsedBatch) -> None:
    for r in batch.records:
        store.record_email(r.uid, r.message_id, r.received_at, r.mentions_found, r.status)


def warn_unreachable(ctx: Context, reason: str) -> bool:
    """Telegram warning about the inbox, at most once every 6 hours."""
    last = parse_dt(ctx.store.get_state(WARNING_STATE_KEY))
    if last is not None and ctx.now - last < timedelta(hours=WARNING_INTERVAL_HOURS):
        log.info("Inbox warning already sent at %s; not repeating", last.isoformat())
        return False
    if ctx.dry_run:
        print(f"[dry-run] would warn: F5Bot inbox unreachable: {reason}")
        return False
    ctx.telegram.send(f"F5Bot inbox unreachable: {reason}", kind="warning")
    ctx.store.set_state(WARNING_STATE_KEY, iso(ctx.now) or "")
    return True


def is_paused(store: Store) -> bool:
    return (store.get_state("paused") or "0") == "1"


def run_scan(ctx: Context, mailbox_factory: Callable[..., Any] = Mailbox) -> RunSummary:
    summary = RunSummary(command="scan")
    try:
        with mailbox_factory(ctx.settings, ctx.store, dry_run=ctx.dry_run) as mailbox:
            emails = mailbox.fetch_new(now=ctx.now)
            batch = parse_batch(ctx.settings, emails, ctx.dry_run, summary)
            process(ctx, batch.mentions, summary, batch.uid_by_mention)
            record_batch(ctx.store, batch)
            mailbox.mark_seen(batch.ok_uids)
    except MailboxError as exc:
        reason = str(exc)
        log.error("F5Bot inbox unreachable: %s", reason)
        summary.errors.append(f"imap: {reason}")
        warn_unreachable(ctx, reason)
        ctx.telegram.resend_failures()
        return summary
    if ctx.store.get_state(WARNING_STATE_KEY) and not ctx.dry_run:
        ctx.store.set_state(WARNING_STATE_KEY, "")  # inbox is back; the next outage warns straight away
    return summary


def run_ingest_eml(ctx: Context, folder: Path) -> RunSummary:
    summary = RunSummary(command="ingest-eml")
    files = sorted(folder.glob("*.eml"))
    emails = [(f"file:{p.name}", p.read_bytes()) for p in files]
    batch = parse_batch(ctx.settings, emails, ctx.dry_run, summary)
    for record in batch.records:
        print(f"  {record.uid[5:]}: {record.status}, {record.mentions_found} mention(s)")
    process(ctx, batch.mentions, summary, batch.uid_by_mention)
    record_batch(ctx.store, batch)
    return summary
