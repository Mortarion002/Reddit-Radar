"""Command line entry points: python -m radar <command>."""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Callable, Sequence

from .config import Settings, load_settings
from .logger import setup_logging

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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _reconfigure_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    settings = load_settings(warn=False)
    setup_logging(settings, level=logging.DEBUG if args.verbose else logging.INFO)
    if not settings.telegram_configured():
        log.warning("Telegram is not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
    if not settings.imap_configured():
        log.warning("IMAP is not configured (IMAP_USER / IMAP_APP_PASSWORD / F5BOT_SENDER)")
    try:
        return int(args.func(args, settings) or 0)
    except KeyboardInterrupt:
        return 130
