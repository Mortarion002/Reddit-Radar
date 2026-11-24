"""Read new F5Bot alert emails from the dedicated Gmail inbox over IMAP (stdlib imaplib).

Read-only except for setting \\Seen on processed mail. Never deletes, moves or sends mail.
The app password is never logged.
"""

from __future__ import annotations

import imaplib
import logging
import socket
import ssl
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from ..config import Settings
from ..store import Store

log = logging.getLogger("radar.imap")

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
IMAP_TIMEOUT = 60


class MailboxError(Exception):
    """Connection, login or folder problems. The message is safe to show (no secrets)."""


def imap_date(day: date) -> str:
    """IMAP SEARCH date (RFC 3501), independent of the OS locale: 25-Nov-2026."""
    return f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"


def search_since(store: Store, settings: Settings, now: datetime) -> date:
    """First run: INGEST_LOOKBACK_DAYS back. Later: the last processed email's date minus 1 day."""
    last = store.last_processed_email_at()
    if last is None:
        return (now - timedelta(days=settings.ingest_lookback_days)).date()
    return (last.astimezone(timezone.utc) - timedelta(days=1)).date()


def _safe_reason(exc: BaseException, settings: Settings) -> str:
    text = str(exc)
    if isinstance(exc, bytes):
        text = exc.decode(errors="replace")
    for secret in settings.secrets():
        text = text.replace(secret, "***")
    text = text.strip().strip("b'\"") or type(exc).__name__
    return text[:200]


@dataclass
class Mailbox:
    settings: Settings
    store: Store
    dry_run: bool = False
    imap_factory: Callable[..., Any] = field(default=None)  # type: ignore[assignment]
    conn: Any = field(default=None, init=False)

    def __enter__(self) -> "Mailbox":
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def connect(self) -> None:
        s = self.settings
        if not s.imap_configured():
            raise MailboxError("IMAP not configured (IMAP_USER / IMAP_APP_PASSWORD / F5BOT_SENDER)")
        factory = self.imap_factory or imaplib.IMAP4_SSL
        try:
            self.conn = factory(s.imap_host, s.imap_port, timeout=IMAP_TIMEOUT)
        except (OSError, socket.timeout, ssl.SSLError, imaplib.IMAP4.error) as exc:
            raise MailboxError(f"cannot connect to {s.imap_host}:{s.imap_port}: {_safe_reason(exc, s)}") from None
        try:
            self.conn.login(s.imap_user, s.imap_app_password)
        except (imaplib.IMAP4.error, OSError) as exc:
            self._logout()
            raise MailboxError(f"login failed for {s.imap_user}: {_safe_reason(exc, s)}") from None
        try:
            typ, data = self.conn.select(s.imap_folder, readonly=self.dry_run or not s.imap_mark_seen)
        except (imaplib.IMAP4.error, OSError) as exc:
            self._logout()
            raise MailboxError(f"cannot open folder {s.imap_folder}: {_safe_reason(exc, s)}") from None
        if typ != "OK":
            self._logout()
            raise MailboxError(f"cannot open folder {s.imap_folder}: {data!r}")

    def _logout(self) -> None:
        if self.conn is None:
            return
        try:
            self.conn.logout()
        except Exception:  # already broken; nothing to clean up
            pass
        self.conn = None

    def close(self) -> None:
        if self.conn is None:
            return
        try:
            self.conn.close()
        except Exception:
            pass
        self._logout()

    def search(self, since: date) -> list[str]:
        try:
            typ, data = self.conn.uid(
                "SEARCH", None, "FROM", f'"{self.settings.f5bot_sender}"', "SINCE", imap_date(since)
            )
        except (imaplib.IMAP4.error, OSError) as exc:
            raise MailboxError(f"search failed: {_safe_reason(exc, self.settings)}") from None
        if typ != "OK":
            raise MailboxError(f"search failed: {data!r}")
        raw = b" ".join(d for d in data if isinstance(d, bytes))
        return [u.decode() for u in raw.split()]

    def fetch(self, uid: str) -> bytes | None:
        """Fetch the full message with BODY.PEEK[] so reading doesn't mark it seen."""
        try:
            typ, data = self.conn.uid("FETCH", uid, "(BODY.PEEK[])")
        except (imaplib.IMAP4.error, OSError) as exc:
            raise MailboxError(f"fetch of {uid} failed: {_safe_reason(exc, self.settings)}") from None
        if typ != "OK":
            log.warning("FETCH %s returned %s", uid, typ)
            return None
        for part in data or []:
            if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], bytes):
                return part[1]
        return None

    def fetch_new(self, now: datetime | None = None) -> list[tuple[str, bytes]]:
        """New F5Bot emails as (uid, raw bytes), skipping UIDs already in processed_emails."""
        since = search_since(self.store, self.settings, now or datetime.now(timezone.utc))
        uids = self.search(since)
        done = self.store.processed_uids(uids)
        new = [u for u in uids if u not in done]
        log.info("IMAP: %d F5Bot email(s) since %s, %d new", len(uids), imap_date(since), len(new))
        out: list[tuple[str, bytes]] = []
        for uid in new:
            raw = self.fetch(uid)
            if raw is not None:
                out.append((uid, raw))
        return out

    def mark_seen(self, uids: list[str]) -> None:
        if self.dry_run or not self.settings.imap_mark_seen or not uids:
            return
        for uid in uids:
            try:
                self.conn.uid("STORE", uid, "+FLAGS", "(\\Seen)")
            except (imaplib.IMAP4.error, OSError) as exc:
                log.warning("Could not mark %s seen: %s", uid, _safe_reason(exc, self.settings))
