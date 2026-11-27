"""Command line entry points: python -m radar <command>."""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Sequence

from .config import Settings, load_settings
from .logger import setup_logging
from .digest import RunSummary
from .drafts import DraftsFileError, send_drafts
from .doctor import format_checks, run_doctor
from .feedback import run_feedback
from .housekeeping import run_housekeeping
from .ingest import is_paused, run_ingest_eml, run_scan
from .models import Lead
from .pipeline import Context, process, resend_pending
from .scorer import ScoringConfig, build_lead
from .sources.sample import SampleError, load_mentions
from .stats import compute_stats, format_stats
from .store import Store
from .telegram import TelegramClient

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


def _context(settings: Settings, store: Store, dry_run: bool, now: datetime) -> Context:
    return Context(settings=settings, store=store, telegram=TelegramClient(settings, dry_run=dry_run), now=now, dry_run=dry_run)


def _finish(store: Store, run_id: int, summary: RunSummary) -> None:
    store.finish_run(
        run_id,
        emails_read=summary.emails_read,
        mentions_found=summary.mentions_found,
        new_mentions=summary.new_mentions,
        leads_high=summary.high,
        leads_medium=summary.medium,
        errors=summary.errors,
    )


def _print_summary(summary: RunSummary, dry_run: bool) -> None:
    prefix = "[dry-run] " if dry_run else ""
    tiers = ", ".join(f"{t} {summary.tiers.get(t, 0)}" for t in ("High", "Medium", "Low", "Dropped"))
    emails = f"{summary.emails_read} emails, " if summary.command != "import" else ""
    print(
        f"{prefix}{summary.command}: {emails}{summary.mentions_found} mentions, {summary.new_mentions} new. "
        f"{tiers}. Hot alerts: {summary.alerts_sent}"
    )
    if summary.parse_errors or summary.skipped_hits:
        print(f"  parse errors: {summary.parse_errors}, skipped hits: {summary.skipped_hits} (see data/logs/radar.log)")


def cmd_import(args: argparse.Namespace, settings: Settings) -> int:
    now = _now()
    try:
        mentions = load_mentions(Path(args.file), now=now)
    except (OSError, SampleError) as exc:
        log.error("import failed: %s", exc)
        return 2
    with Store.open(settings.db_path, dry_run=args.dry_run) as store:
        run_id = store.start_run("import", now)
        summary = process(_context(settings, store, args.dry_run, now), mentions, RunSummary(command="import"))
        _finish(store, run_id, summary)
    _print_summary(summary, args.dry_run)
    return 0


def cmd_scan(args: argparse.Namespace, settings: Settings) -> int:
    now = _now()
    with Store.open(settings.db_path, dry_run=args.dry_run) as store:
        if is_paused(store):
            print("scan skipped: paused (send /resume to the bot)")
            log.info("scan skipped: paused")
            return 0
        run_id = store.start_run("scan", now)
        summary = run_scan(_context(settings, store, args.dry_run, now))
        _finish(store, run_id, summary)
    try:
        run_housekeeping(settings, now, dry_run=args.dry_run)
    except OSError as exc:
        log.warning("Housekeeping failed: %s", exc)
    _print_summary(summary, args.dry_run)
    for error in summary.errors:
        print(f"  error: {error}")
    return 0


def cmd_ingest_eml(args: argparse.Namespace, settings: Settings) -> int:
    folder = Path(args.dir)
    if not folder.is_dir():
        print(f"not a folder: {folder}")
        return 2
    now = _now()
    with Store.open(settings.db_path, dry_run=args.dry_run) as store:
        run_id = store.start_run("ingest-eml", now)
        summary = run_ingest_eml(_context(settings, store, args.dry_run, now), folder)
        _finish(store, run_id, summary)
    _print_summary(summary, args.dry_run)
    return 0


def cmd_send_drafts(args: argparse.Namespace, settings: Settings) -> int:
    now = _now()
    path = Path(args.file) if args.file else None
    with Store.open(settings.db_path, dry_run=args.dry_run) as store:
        try:
            result = send_drafts(_context(settings, store, args.dry_run, now), path)
        except DraftsFileError as exc:
            print(f"send-drafts: {exc}")
            return 2
    prefix = "[dry-run] " if args.dry_run else ""
    if result.resent:
        print(f"{prefix}resent {result.resent} message(s) that failed to send earlier")
    for stale in result.stale:
        print(f"{prefix}{stale.name}: older than {settings.max_lead_age_hours}h, leads expired; not sent")
    if result.path is None:
        print(f"{prefix}no unsent drafts file in {settings.drafts_dir}")
    elif result.already_sent:
        print(f"{prefix}{result.path.name} was already sent; skipped")
    elif result.files:
        names = ", ".join(f.name for f in result.files)
        print(
            f"{prefix}{names}: sent {result.sent} draft(s), {result.flagged} flagged NEEDS EDIT, "
            f"{result.skipped_empty} skipped lead(s)"
        )
    if result.queued:
        print(
            f"{prefix}{result.queued} draft(s) could not reach Telegram; saved in data/delivery_failures/ "
            "and resent automatically on the next scan, feedback or send-drafts run"
        )
    if result.duplicates:
        print(f"{prefix}{result.duplicates} draft(s) were already handled earlier; not sent twice")
    return 0


def cmd_feedback(args: argparse.Namespace, settings: Settings) -> int:
    now = _now()
    with Store.open(settings.db_path, dry_run=args.dry_run) as store:
        result = run_feedback(_context(settings, store, args.dry_run, now))
    prefix = "[dry-run] " if args.dry_run else ""
    print(
        f"{prefix}feedback: {result.updates} update(s), {len(result.statuses)} status change(s), "
        f"commands: {', '.join(result.commands) or 'none'}"
    )
    for mention_id, status in result.statuses:
        print(f"  {mention_id} -> {status}")
    return 0


def cmd_stats(args: argparse.Namespace, settings: Settings) -> int:
    now = _now()
    if not settings.db_path.exists():
        print("no database yet; run scan or import first")
        return 0
    with Store.open(settings.db_path, dry_run=True) as store:
        text = format_stats(compute_stats(store, args.days, now))
    print(re.sub(r"<[^>]+>", "", text).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))
    if not args.no_send:
        with Store.open(settings.db_path, dry_run=args.dry_run) as store:
            ctx = _context(settings, store, args.dry_run, now)
            resend_pending(ctx)
            ctx.telegram.send(text, kind="stats")
    return 0


def cmd_doctor(args: argparse.Namespace, settings: Settings) -> int:
    checks = run_doctor(settings, _now(), dry_run=args.dry_run)
    print(format_checks(checks))
    failed = [c for c in checks if not c.ok]
    print("all checks passed" if not failed else f"{len(failed)} check(s) failed")
    return 1 if failed else 0


def cmd_housekeeping(args: argparse.Namespace, settings: Settings) -> int:
    result = run_housekeeping(settings, _now(), dry_run=args.dry_run)
    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}housekeeping: " + ", ".join(f"{k} {v}" for k, v in result.items()))
    return 0


def cmd_drafts_pending(args: argparse.Namespace, settings: Settings) -> int:
    """Print how many leads in the leads file still need a draft (used by scripts/run_drafts.ps1)."""
    path = Path(args.file) if args.file else settings.leads_dir / "latest.json"
    try:
        leads = json.loads(path.read_text(encoding="utf-8")).get("leads", [])
    except (OSError, json.JSONDecodeError, AttributeError):
        print(0)
        return 0
    if not settings.db_path.exists():
        print(len(leads))
        return 0
    with Store.open(settings.db_path, dry_run=True) as store:
        pending = 0
        for item in leads:
            mid = str(item.get("mention_id", ""))
            lead = store.get_lead(mid)
            if not store.has_draft(mid) and (lead is None or lead.status == "new"):
                pending += 1
    print(pending)
    return 0


def cmd_notify(args: argparse.Namespace, settings: Settings) -> int:
    """Send one plain line to Telegram (used by the PowerShell scripts)."""
    client = TelegramClient(settings, dry_run=args.dry_run)
    client.send(html.escape(args.text, quote=False), kind="notice")
    return 0


def cmd_telegram_test(args: argparse.Namespace, settings: Settings) -> int:
    client = TelegramClient(settings, dry_run=args.dry_run)
    if not client.configured and not args.dry_run:
        print("Telegram is not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return 1
    message_id = client.send("Reddit Radar connected", kind="test", preserve=False)
    if args.dry_run:
        return 0
    if message_id is None:
        print("Telegram test failed; see data/logs/radar.log")
        return 1
    print(f"sent (message_id {message_id})")
    if client.pending_failures():
        with Store.open(settings.db_path) as store:
            ctx = Context(settings=settings, store=store, telegram=client, now=_now(), dry_run=False)
            sent, left = resend_pending(ctx)
        print(f"resent {sent} message(s) waiting in data/delivery_failures/, {left} still waiting")
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

    p = sub.add_parser("scan", parents=[common], help="read new F5Bot emails over IMAP and process them")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("ingest-eml", parents=[common], help="parse saved F5Bot .eml files from a folder")
    p.add_argument("--dir", required=True, help="folder with .eml files")
    p.set_defaults(func=cmd_ingest_eml)

    p = sub.add_parser("import", parents=[common], help="load mentions from a JSON file, score and store them")
    p.add_argument("--file", required=True, help="path to a mentions JSON file")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("send-drafts", parents=[common], help="validate reply drafts and send them to Telegram")
    p.add_argument("--file", help="drafts JSON (default: newest unsent file in data/drafts/)")
    p.set_defaults(func=cmd_send_drafts)

    p = sub.add_parser("feedback", parents=[common], help="read Telegram button taps and /stats /pause /resume")
    p.set_defaults(func=cmd_feedback)

    p = sub.add_parser("stats", parents=[common], help="weekly stats report (printed and sent to Telegram)")
    p.add_argument("--days", type=float, default=7, help="window in days (default 7)")
    p.add_argument("--no-send", action="store_true", help="print only, don't send to Telegram")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("doctor", parents=[common], help="check .env, Telegram, IMAP, F5Bot, DB and claude")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("housekeeping", parents=[common], help="delete old leads/drafts/unparsed files")
    p.set_defaults(func=cmd_housekeeping)

    p = sub.add_parser("drafts-pending", parents=[common], help="print how many exported leads still need a draft")
    p.add_argument("--file", help="leads JSON (default data/leads/latest.json)")
    p.set_defaults(func=cmd_drafts_pending)

    p = sub.add_parser("notify", parents=[common], help="send one line of text to Telegram")
    p.add_argument("--text", required=True)
    p.set_defaults(func=cmd_notify)

    p = sub.add_parser("telegram-test", parents=[common], help='send "Reddit Radar connected" to Telegram')
    p.set_defaults(func=cmd_telegram_test)

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
