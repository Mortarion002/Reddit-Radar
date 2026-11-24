"""Command line entry points: python -m radar <command>."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Sequence

from .config import Settings, load_settings
from .logger import setup_logging
from .models import Lead, Mention
from .scorer import ScoringConfig, build_lead
from .sources.sample import SampleError, load_mentions
from .store import Store

log = logging.getLogger("radar.cli")

Handler = Callable[[argparse.Namespace, Settings], int]


def _reconfigure_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


# ---- commands --------------------------------------------------------------


def cmd_config_check(args: argparse.Namespace, settings: Settings) -> int:
    print(f"env file: {settings.env_file or '(none found, using environment only)'}")
    for key, value in settings.masked().items():
        print(f"  {key:<22} {value}")
    signals = settings.signals
    competitors = signals.get("competitors", {}) or {}
    print(
        "signals.yaml: "
        f"{len(competitors.get('priority', []) or [])} priority competitors, "
        f"{len(competitors.get('tracked', []) or [])} tracked, "
        f"{len(signals.get('product_terms', []) or [])} product terms"
    )
    bonus = settings.subreddits.get("bonus", {}) or {}
    blocklist = settings.subreddits.get("blocklist", []) or []
    print(f"subreddits.yaml: {len(bonus)} bonuses, {len(blocklist)} blocked")
    missing = settings.missing()
    if missing:
        print("missing: " + ", ".join(missing))
    else:
        print("missing: none")
    return 0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_label(hours: float) -> str:
    if hours < 1:
        return f"{max(0, int(hours * 60))}m"
    if hours < 48:
        return f"{hours:.0f}h"
    return f"{hours / 24:.0f}d"


def score_and_store(store: Store, mentions: list[Mention], cfg: ScoringConfig, now: datetime) -> list[Lead]:
    """Dedupe, store and score. Returns leads for the mentions not seen before."""
    new = store.upsert_mentions(mentions, now=now)
    leads = [build_lead(m, cfg, now) for m in new]
    for lead in leads:
        store.save_lead(lead)
    return leads


def _tier_summary(leads: list[Lead]) -> str:
    counts = {t: 0 for t in ("High", "Medium", "Low", "Dropped")}
    for lead in leads:
        counts[lead.tier] = counts.get(lead.tier, 0) + 1
    return ", ".join(f"{k} {v}" for k, v in counts.items())


def cmd_import(args: argparse.Namespace, settings: Settings) -> int:
    now = _now()
    try:
        mentions = load_mentions(Path(args.file), now=now)
    except (OSError, SampleError) as exc:
        log.error("import failed: %s", exc)
        return 2
    cfg = ScoringConfig.from_settings(settings)
    with Store.open(settings.db_path, dry_run=args.dry_run) as store:
        run_id = store.start_run("import", now)
        leads = score_and_store(store, mentions, cfg, now)
        counts = {t: sum(1 for l in leads if l.tier == t) for t in ("High", "Medium")}
        store.finish_run(
            run_id,
            mentions_found=len(mentions),
            new_mentions=len(leads),
            leads_high=counts["High"],
            leads_medium=counts["Medium"],
        )
    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}loaded {len(mentions)} mentions, {len(leads)} new. {_tier_summary(leads)}")
    return 0


def _print_leads(leads: list[Lead], now: datetime) -> None:
    if not leads:
        print("no leads")
        return
    print(f"{'TIER':<7}{'SCORE':>5}  {'SIGNAL':<19}{'SUBREDDIT':<18}{'KIND':<8}{'AGE':>4}  {'STATUS':<12}TITLE / URL")
    for lead in leads:
        m = lead.mention
        hours = (now - m.received_at).total_seconds() / 3600
        title = m.title if len(m.title) <= 70 else m.title[:67] + "..."
        print(
            f"{lead.tier:<7}{lead.score:>5.0f}  {lead.primary_signal:<19}{('r/' + m.subreddit)[:17]:<18}"
            f"{m.kind:<8}{_age_label(hours):>4}  {lead.status:<12}{title}"
        )
        print(f"{'':<73}{m.url}")


def cmd_leads(args: argparse.Namespace, settings: Settings) -> int:
    tiers = [t.strip().capitalize() for t in args.tier.split(",") if t.strip()] if args.tier else None
    now = _now()
    since = now - timedelta(days=args.days) if args.days else None
    if not settings.db_path.exists():
        print("no database yet; run import or scan first")
        return 0
    with Store.open(settings.db_path, dry_run=True) as store:
        _print_leads(store.get_leads(tiers=tiers, since=since), now)
    return 0


def cmd_rescore(args: argparse.Namespace, settings: Settings) -> int:
    """Re-score stored mentions with the current config, as of when each was first seen."""
    cfg = ScoringConfig.from_settings(settings)
    since = _now() - timedelta(days=args.days)
    changed = 0
    with Store.open(settings.db_path, dry_run=args.dry_run) as store:
        rows = store.get_mentions(since=since)
        for mention, first_seen in rows:
            old = store.get_lead(mention.mention_id)
            lead = build_lead(mention, cfg, first_seen)
            if old is None or old.tier != lead.tier or old.score != lead.score:
                changed += 1
                if old is not None:
                    log.info(
                        "%s: %s %.0f -> %s %.0f", mention.mention_id, old.tier, old.score, lead.tier, lead.score
                    )
            store.save_lead(lead)
    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}rescored {len(rows)} mentions from the last {args.days} days, {changed} changed")
    return 0


# ---- parser ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--dry-run",
        action="store_true",
        help="no Telegram sends, no DB writes, no mailbox changes",
    )
    common.add_argument("-v", "--verbose", action="store_true", help="debug logging on the console")

    parser = argparse.ArgumentParser(
        prog="python -m radar",
        description="Reddit Radar: F5Bot email alerts -> scored leads -> Telegram.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser("config-check", parents=[common], help="print settings with secrets masked")
    p.set_defaults(func=cmd_config_check)

    p = sub.add_parser("import", parents=[common], help="load mentions from a JSON file, score and store them")
    p.add_argument("--file", required=True, help="path to a mentions JSON file")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("leads", parents=[common], help="print stored leads as a table")
    p.add_argument("--tier", default="High,Medium", help="comma list, e.g. High,Medium (default)")
    p.add_argument("--days", type=float, default=3, help="only leads received in the last N days (default 3)")
    p.set_defaults(func=cmd_leads)

    p = sub.add_parser("rescore", parents=[common], help="re-score stored mentions with the current config")
    p.add_argument("--days", type=float, default=7, help="mentions received in the last N days (default 7)")
    p.set_defaults(func=cmd_rescore)

    return parser


def main(argv: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    _reconfigure_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    settings = settings or load_settings(warn=False)
    setup_logging(settings, level=logging.DEBUG if args.verbose else logging.INFO)
    if not settings.telegram_configured():
        log.warning("Telegram is not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
    if not settings.imap_configured():
        log.warning("IMAP is not configured (IMAP_USER / IMAP_APP_PASSWORD / F5BOT_SENDER)")
    try:
        return int(args.func(args, settings) or 0)
    except KeyboardInterrupt:
        return 130
