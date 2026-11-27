"""Telegram Bot API over plain HTTPS. Failed sends are preserved to data/delivery_failures/.

Some ISPs (including Aman's) don't resolve api.telegram.org. When a plain request fails
with a DNS error, the client resolves the host through 1.1.1.1 with nslookup and retries
with `curl --resolve` (ported from reference/notifier.py). The token is never logged.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

import requests

from .config import Settings

log = logging.getLogger("radar.telegram")

TELEGRAM_HOST = "api.telegram.org"
MESSAGE_LIMIT = 4000
RETRY_DELAY_SECONDS = 5
REQUEST_TIMEOUT = 20
DNS_FALLBACK_SERVER = "1.1.1.1"
META_PREFIX = "RADAR-META "
RESEND_PAUSE_SECONDS = 0.5  # spacing between resent messages, to stay under Telegram's rate limit
MAX_RATE_LIMIT_WAIT = 60  # longest we honour a 429 retry_after, in seconds
STALE_CLAIM_SECONDS = 15 * 60  # a ".sending" file older than this belongs to a crashed run
DEAD_DIR = "dead"  # messages Telegram rejects for good are moved here, out of the queue
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_DNS_MARKERS = (
    "failed to resolve",
    "name resolution",
    "getaddrinfo failed",
    "nodename nor servname",
    "name or service not known",
    "temporary failure in name resolution",
)

Buttons = Sequence[Sequence[tuple[str, str]]]  # rows of (label, callback_data)


class TelegramError(Exception):
    """A failed Bot API call. The message never contains the token.

    network: the request didn't reach Telegram (no internet, DNS, timeout) -> worth retrying later.
    status: Telegram's HTTP status when it answered with an error (400 bad request, 429 rate limit...).
    """

    def __init__(
        self, message: str, *, network: bool = False, status: int | None = None, retry_after: int | None = None
    ) -> None:
        super().__init__(message)
        self.network = network
        self.status = status
        self.retry_after = retry_after

    @property
    def permanent(self) -> bool:
        """Telegram rejected this particular message (HTTP 400); resending it unchanged won't help."""
        return self.status == 400


def looks_like_dns_failure(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _DNS_MARKERS)


def split_message(message: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split on newlines (or spaces) so each chunk is at most `limit` characters."""
    message = message.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()
    if not message:
        return []
    chunks: list[str] = []
    remaining = message
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at < limit // 2:
            split_at = limit
        chunk = remaining[:split_at].strip()
        if not chunk:
            chunk, split_at = remaining[:limit], limit
        chunks.append(chunk)
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def keyboard(buttons: Buttons | None) -> dict[str, Any] | None:
    if not buttons:
        return None
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]}


@dataclass
class TelegramClient:
    settings: Settings
    dry_run: bool = False
    http: Any = requests
    sleep: Callable[[float], None] = time.sleep
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run
    _use_curl: bool = field(default=False, init=False)
    _resolved_ip: str | None = field(default=None, init=False)
    # Set after a send fails because Telegram can't be reached. Later sends in the same run go
    # straight to delivery_failures/ instead of each waiting out timeouts and retries.
    _offline: bool = field(default=False, init=False)

    # ---- low level -----------------------------------------------------------

    @property
    def configured(self) -> bool:
        return self.settings.telegram_configured()

    def _url(self, method: str) -> str:
        return f"https://{TELEGRAM_HOST}/bot{self.settings.telegram_bot_token}/{method}"

    def call(self, method: str, payload: dict[str, Any]) -> Any:
        """One Bot API call. Returns `result`. Raises TelegramError (never with the token in it)."""
        if not self._use_curl:
            try:
                resp = self.http.post(self._url(method), json=payload, timeout=REQUEST_TIMEOUT)
            except requests.exceptions.RequestException as exc:
                if not looks_like_dns_failure(exc):
                    raise TelegramError(f"{method}: {type(exc).__name__}", network=True) from None
                log.info("Telegram DNS lookup failed; switching to the curl fallback via %s", DNS_FALLBACK_SERVER)
                self._use_curl = True
            else:
                return self._parse(method, resp.status_code, resp.text)
        return self._call_curl(method, payload)

    @staticmethod
    def _parse(method: str, status: int, body: str) -> Any:
        try:
            data = json.loads(body or "{}")
        except json.JSONDecodeError:
            raise TelegramError(f"{method}: HTTP {status}, non-JSON response", network=status == 0 or status >= 500) from None
        if not data.get("ok"):
            params = data.get("parameters") or {}
            retry_after = params.get("retry_after") if isinstance(params, dict) else None
            raise TelegramError(
                f"{method}: HTTP {status}: {data.get('description', 'unknown error')}",
                network=status >= 500,  # Telegram's own outage: treat like no connection
                status=status,
                retry_after=int(retry_after) if isinstance(retry_after, (int, float)) else None,
            )
        return data.get("result")

    def _resolve_ip(self) -> str:
        if self._resolved_ip:
            return self._resolved_ip
        try:
            out = self.runner(
                ["nslookup", TELEGRAM_HOST, DNS_FALLBACK_SERVER],
                capture_output=True, text=True, timeout=REQUEST_TIMEOUT, check=False,
            ).stdout or ""
        except (OSError, subprocess.SubprocessError) as exc:
            raise TelegramError(f"DNS fallback lookup failed: {type(exc).__name__}", network=True) from None
        # nslookup prints the server address first; skip it.
        ips = [ip for ip in _IPV4.findall(out) if ip != DNS_FALLBACK_SERVER]
        if not ips:
            raise TelegramError("DNS fallback could not resolve api.telegram.org", network=True)
        self._resolved_ip = ips[0]
        return ips[0]

    def _call_curl(self, method: str, payload: dict[str, Any]) -> Any:
        curl = shutil.which("curl.exe") or shutil.which("curl")
        if not curl:
            raise TelegramError("curl not found for the DNS fallback", network=True)
        ip = self._resolve_ip()
        cmd = [
            curl, "--silent", "--show-error", "--max-time", str(REQUEST_TIMEOUT),
            "--resolve", f"{TELEGRAM_HOST}:443:{ip}",
            "-H", "Content-Type: application/json", "--data-binary", "@-",
            "-w", "\n%{http_code}", self._url(method),
        ]
        try:
            proc = self.runner(
                cmd, input=json.dumps(payload), capture_output=True, text=True,
                encoding="utf-8", timeout=REQUEST_TIMEOUT + 10, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise TelegramError(f"{method}: curl failed: {type(exc).__name__}", network=True) from None
        if proc.returncode != 0:
            raise TelegramError(f"{method}: curl exit {proc.returncode}", network=True)
        body, _, code = (proc.stdout or "").rpartition("\n")
        return self._parse(method, int(code) if code.strip().isdigit() else 0, body)

    # ---- sending -------------------------------------------------------------

    def _send_chunk(self, text: str, markup: dict[str, Any] | None, silent: bool) -> int:
        if self._offline:
            raise TelegramError("Telegram unreachable earlier in this run; not retrying now", network=True)
        payload: dict[str, Any] = {
            "chat_id": self.settings.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": silent,
        }
        if markup:
            payload["reply_markup"] = markup
        failures = rate_waits = 0
        while True:
            try:
                result = self.call("sendMessage", payload)
                return int(result.get("message_id", 0)) if isinstance(result, dict) else 0
            except TelegramError as exc:
                if exc.status == 429 and rate_waits < 3:
                    # Rate limited (e.g. resending a backlog): wait as told, doesn't count as a failure.
                    rate_waits += 1
                    wait = min(exc.retry_after or RETRY_DELAY_SECONDS, MAX_RATE_LIMIT_WAIT)
                    log.warning("Telegram rate limit; waiting %ss", wait)
                    self.sleep(wait)
                    continue
                if "can't parse entities" in str(exc) and payload.get("parse_mode"):
                    log.warning("Telegram rejected the HTML; resending as plain text")
                    payload.pop("parse_mode")
                    payload["text"] = re.sub(r"<[^>]+>", "", text)
                    continue
                failures += 1
                if failures >= 2 or exc.permanent:
                    if exc.network:
                        self._offline = True
                    raise
                log.warning("Telegram send failed (%s); retrying in %ss", exc, RETRY_DELAY_SECONDS)
                self.sleep(RETRY_DELAY_SECONDS)

    def _deliver(self, text: str, buttons: Buttons | None, silent: bool) -> int:
        """Send all chunks; buttons go on the last one. Raises TelegramError with the unsent rest."""
        chunks = split_message(text)
        if not chunks:
            raise TelegramError("empty message")
        message_id = 0
        for index, chunk in enumerate(chunks):
            is_last = index == len(chunks) - 1
            try:
                message_id = self._send_chunk(chunk, keyboard(buttons) if is_last else None, silent)
            except TelegramError as exc:
                exc.unsent = "\n".join(chunks[index:])  # type: ignore[attr-defined]
                raise
        return message_id

    def send(
        self,
        text: str,
        buttons: Buttons | None = None,
        kind: str = "message",
        silent: bool = False,
        preserve: bool = True,
        meta: dict[str, Any] | None = None,
    ) -> int | None:
        """Send an HTML message. Returns the message_id, or None if it was not delivered.

        An undelivered message is saved to data/delivery_failures/ (with its buttons and `meta`,
        e.g. the draft id) and resent by the next command that sends anything.
        """
        if self.dry_run:
            print(f"[dry-run] telegram {kind}:\n{text}\n")
            return None
        if not self.configured:
            log.warning("Telegram not configured; %s not sent", kind)
            if preserve:
                self.preserve(text, buttons, kind, meta)
            return None
        try:
            return self._deliver(text, buttons, silent)
        except TelegramError as exc:
            log.error("Telegram %s not delivered: %s", kind, exc)
            if preserve:
                path = self.preserve(getattr(exc, "unsent", text), buttons, kind, meta)
                log.info("Saved undelivered %s to %s; it will be resent on the next run", kind, path)
            return None

    # ---- feedback (stage 6) ---------------------------------------------------

    def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        """New updates (button taps and messages). Never long-polls (timeout=0)."""
        if not self.configured:
            log.warning("Telegram not configured; no updates read")
            return []
        payload: dict[str, Any] = {"timeout": 0, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        result = self.call("getUpdates", payload)
        return list(result) if isinstance(result, list) else []

    def answer_callback(self, callback_id: str, text: str) -> bool:
        if self.dry_run:
            print(f"[dry-run] would answer callback: {text}")
            return False
        try:
            self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})
            return True
        except TelegramError as exc:
            log.warning("answerCallbackQuery failed: %s", exc)
            return False

    def edit_buttons(self, chat_id: str, message_id: int, buttons: Buttons) -> bool:
        if self.dry_run:
            print(f"[dry-run] would update buttons on message {message_id}")
            return False
        try:
            self.call(
                "editMessageReplyMarkup",
                {"chat_id": chat_id, "message_id": message_id, "reply_markup": keyboard(buttons)},
            )
            return True
        except TelegramError as exc:
            if "message is not modified" in str(exc):
                return True
            log.warning("editMessageReplyMarkup failed: %s", exc)
            return False

    # ---- failures --------------------------------------------------------------

    def preserve(
        self, text: str, buttons: Buttons | None, kind: str, meta: dict[str, Any] | None = None
    ) -> Path:
        folder = self.settings.delivery_failures_dir
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-z0-9_-]+", "-", kind.lower()).strip("-") or "message"
        stamp = datetime.now(self.settings.zoneinfo()).strftime("%Y%m%d_%H%M%S_%f")
        path = folder / f"{stamp}_{safe}.txt"
        header = dict(meta or {})
        header.update({"kind": kind, "buttons": [list(map(list, row)) for row in buttons] if buttons else None})
        tmp = path.with_suffix(".tmp")
        tmp.write_text(META_PREFIX + json.dumps(header) + "\n\n" + text, encoding="utf-8")
        os.replace(tmp, path)  # never leave a half-written file in the queue
        return path

    @staticmethod
    def load_failure(path: Path) -> tuple[str, Buttons | None, str, dict[str, Any]]:
        """(text, buttons, kind, meta) of a preserved message. Plain hand-written files work too."""
        raw = path.read_text(encoding="utf-8")
        name = path.name.split(".", 1)[0]
        kind = name.split("_", 3)[-1] if "_" in name else "message"
        if raw.startswith(META_PREFIX):
            header, _, text = raw.partition("\n\n")
            try:
                meta = json.loads(header[len(META_PREFIX):])
            except json.JSONDecodeError:
                return raw, None, kind, {}
            buttons = [[(t, d) for t, d in row] for row in meta.get("buttons") or []] or None
            return text, buttons, meta.get("kind") or kind, meta
        return raw, None, kind, {}

    @staticmethod
    def read_failure(path: Path) -> tuple[str, Buttons | None, str]:
        text, buttons, kind, _ = TelegramClient.load_failure(path)
        return text, buttons, kind

    def pending_failures(self) -> list[Path]:
        folder = self.settings.delivery_failures_dir
        return sorted(folder.glob("*.txt")) if folder.exists() else []

    def dead_failures(self) -> list[Path]:
        folder = self.settings.delivery_failures_dir / DEAD_DIR
        return sorted(folder.glob("*.txt")) if folder.exists() else []

    def _recover_stale_claims(self) -> None:
        """Put back files a crashed run had claimed (renamed to .sending) but never finished."""
        folder = self.settings.delivery_failures_dir
        now = time.time()
        for claimed in folder.glob("*.sending"):
            try:
                if now - claimed.stat().st_mtime > STALE_CLAIM_SECONDS:
                    os.replace(claimed, claimed.with_suffix(".txt"))
                    log.info("Recovered %s from an interrupted run", claimed.name)
            except OSError:
                pass

    def _bury(self, path: Path, reason: str) -> None:
        dead = self.settings.delivery_failures_dir / DEAD_DIR
        dead.mkdir(parents=True, exist_ok=True)
        target = dead / (path.name.split(".", 1)[0] + ".txt")
        os.replace(path, target)
        log.error("Telegram refused %s for good (%s); moved to %s", path.name, reason, target)

    def resend_failures(self, on_sent: Callable[[dict[str, Any], int], None] | None = None) -> tuple[int, int]:
        """Resend preserved messages, oldest first, deleting each once sent. Returns (sent, left).

        - Each file is claimed (renamed to .sending) first, so two runs at once never send it twice.
        - If Telegram can't be reached, stop and keep the rest for the next run.
        - If Telegram rejects a message outright (HTTP 400), move it to delivery_failures/dead/ so it
          can't block everything queued behind it.
        - on_sent(meta, message_id) lets the caller record the delivery (e.g. the draft's message id).
        """
        folder = self.settings.delivery_failures_dir
        if not folder.exists():
            return 0, 0
        self._recover_stale_claims()
        files = self.pending_failures()
        if not files:
            return 0, 0
        if self.dry_run:
            print(f"[dry-run] would resend {len(files)} preserved message(s)")
            return 0, len(files)
        if not self.configured or self._offline:
            return 0, len(files)
        sent = 0
        for index, path in enumerate(files):
            claimed = path.with_suffix(".sending")
            try:
                os.replace(path, claimed)
            except OSError:
                continue  # another run took it
            try:
                text, buttons, kind, meta = self.load_failure(claimed)
            except (OSError, UnicodeDecodeError) as exc:
                self._bury(claimed, f"unreadable: {exc}")
                continue
            if index:
                self.sleep(RESEND_PAUSE_SECONDS)
            try:
                message_id = self._deliver(text, buttons, silent=False)
            except TelegramError as exc:
                if exc.permanent:
                    self._bury(claimed, str(exc))
                    continue
                os.replace(claimed, path)
                log.warning("Resend of %s failed (%s); keeping it for the next run", path.name, exc)
                break
            claimed.unlink(missing_ok=True)
            sent += 1
            if on_sent is not None:
                try:
                    on_sent(meta, message_id)
                except Exception:  # recording the delivery must never undo it
                    log.exception("Could not record delivery of %s", path.name)
        left = len(self.pending_failures())
        if sent:
            log.info("Resent %d preserved message(s); %d still waiting", sent, left)
        return sent, left
