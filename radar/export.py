"""Leads file for Claude Code: data/leads/YYYY-MM-DD_HHMM.json plus latest.json (spec section 10)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import Settings
from .digest import lead_age_hours
from .store import Store

log = logging.getLogger("radar.export")

EXPORT_CAP = 10
RATIO_WINDOW_DAYS = 7


def stamp_for(generated_at: datetime) -> str:
    return generated_at.strftime("%Y-%m-%d_%H%M")


def build_payload(store: Store, settings: Settings, now: datetime) -> tuple[dict[str, Any], list[str]]:
    """The leads payload and the mention_ids it includes."""
    tz = settings.zoneinfo()
    leads = store.exportable_leads(now - timedelta(hours=settings.max_lead_age_hours), EXPORT_CAP)
    replies, mentions = store.reply_stats(now - timedelta(days=RATIO_WINDOW_DAYS))
    payload = {
        "generated_at": now.astimezone(tz).isoformat(timespec="seconds"),
        "elvan_mentions_last_7_days": mentions,
        "replies_last_7_days": replies,
        "leads": [
            {
                "mention_id": l.mention_id,
                "kind": l.mention.kind,
                "post_id": l.mention.post_id,
                "subreddit": l.mention.subreddit,
                "thread_title": l.mention.title,
                "snippet": l.mention.snippet,
                "url": l.mention.url,
                "age_hours": round(lead_age_hours(l, now), 1),
                "keyword": l.mention.keyword,
                "primary_signal": l.primary_signal,
                "signals": l.signals,
                "competitor": l.competitor,
                "score": round(l.score),
                "tier": l.tier,
            }
            for l in leads
        ],
    }
    return payload, [l.mention_id for l in leads]


def export_leads(store: Store, settings: Settings, now: datetime, dry_run: bool = False) -> Path | None:
    """Write the stamped file (only when there are leads) and always refresh latest.json.

    Returns the path of the stamped file, or latest.json when there were no leads.
    """
    payload, ids = build_payload(store, settings, now)
    folder = settings.leads_dir
    stamped = folder / f"{stamp_for(now.astimezone(settings.zoneinfo()))}.json"
    latest = folder / "latest.json"
    if dry_run:
        print(f"[dry-run] would write {len(ids)} lead(s) to {stamped if ids else latest}")
        return None
    folder.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if ids:
        stamped.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    store.mark("exported_at", ids, now)
    log.info("Exported %d lead(s) to %s", len(ids), stamped if ids else latest)
    return stamped if ids else latest
