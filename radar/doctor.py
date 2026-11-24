"""`radar doctor`: pass/fail health checks for an unattended setup."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from .config import Settings
from .sources.f5bot_mail import Mailbox, MailboxError
from .store import Store, iso
from .telegram import TelegramClient, TelegramError


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def check_env(settings: Settings) -> Check:
    if settings.env_file is None:
        return Check(".env present", False, f"no .env in {settings.base_dir} (copy .env.example)")
    missing = settings.missing()
    if missing:
        return Check(".env present", False, "missing or placeholder: " + ", ".join(missing))
    return Check(".env present", True, str(settings.env_file))


def check_telegram(client: TelegramClient) -> Check:
    if not client.configured:
        return Check("Telegram reachable", False, "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
    try:
        me = client.call("getMe", {})
    except TelegramError as exc:
        return Check("Telegram reachable", False, str(exc))
    name = me.get("username", "?") if isinstance(me, dict) else "?"
    via = " (via the DNS fallback)" if getattr(client, "_use_curl", False) else ""
    return Check("Telegram reachable", True, f"bot @{name}{via}")


def check_imap(settings: Settings, store: Store, now: datetime, mailbox_factory: Callable[..., Any]) -> list[Check]:
    try:
        with mailbox_factory(settings, store, dry_run=True) as mailbox:
            login = Check("IMAP login", True, f"{settings.imap_user} on {settings.imap_host}")
            uids = mailbox.search((now - timedelta(days=7)).date())
    except MailboxError as exc:
        return [
            Check("IMAP login", False, str(exc)),
            Check("F5Bot emails in last 7 days", False, "skipped: no IMAP connection"),
        ]
    if uids:
        recent = Check("F5Bot emails in last 7 days", True, f"{len(uids)} from {settings.f5bot_sender}")
    else:
        recent = Check(
            "F5Bot emails in last 7 days", False, f"none from {settings.f5bot_sender}: is F5Bot still sending?"
        )
    return [login, recent]


def check_db(settings: Settings, now: datetime, dry_run: bool) -> Check:
    if dry_run:
        return Check("DB writable", True, "skipped (dry-run)")
    try:
        with Store.open(settings.db_path) as store:
            store.set_state("doctor_at", iso(now) or "")
    except Exception as exc:  # sqlite3.Error, OSError
        return Check("DB writable", False, f"{settings.db_path}: {exc}")
    return Check("DB writable", True, str(settings.db_path))


def check_claude(which: Callable[[str], str | None] = shutil.which) -> Check:
    path = which("claude")
    if path:
        return Check("claude on PATH", True, path)
    return Check("claude on PATH", False, "not found: install Claude Code CLI and log in (`claude --version`)")


def run_doctor(
    settings: Settings,
    now: datetime,
    dry_run: bool = False,
    telegram: TelegramClient | None = None,
    mailbox_factory: Callable[..., Any] = Mailbox,
    which: Callable[[str], str | None] = shutil.which,
) -> list[Check]:
    checks = [check_env(settings), check_telegram(telegram or TelegramClient(settings))]
    with Store.open(settings.db_path, dry_run=True) as store:
        checks += check_imap(settings, store, now, mailbox_factory)
    checks.append(check_db(settings, now, dry_run))
    checks.append(check_claude(which))
    return checks


def format_checks(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    return "\n".join(f"[{'PASS' if c.ok else 'FAIL'}] {c.name:<{width}}  {c.detail}" for c in checks)
