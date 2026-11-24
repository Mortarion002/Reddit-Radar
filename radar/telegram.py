"""Telegram Bot API over plain HTTPS. Failed sends are preserved to data/delivery_failures/.

Some ISPs (including Aman's) don't resolve api.telegram.org. When a plain request fails
with a DNS error, the client resolves the host through 1.1.1.1 with nslookup and retries
with `curl --resolve` (ported from reference/notifier.py). The token is never logged.
"""

from __future__ import annotations

import json
import logging
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
    pass


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
                    raise TelegramError(f"{method}: {type(exc).__name__}") from None
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
            raise TelegramError(f"{method}: HTTP {status}, non-JSON response") from None
        if not data.get("ok"):
            raise TelegramError(f"{method}: HTTP {status}: {data.get('description', 'unknown error')}")
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
            raise TelegramError(f"DNS fallback lookup failed: {type(exc).__name__}") from None
        # nslookup prints the server address first; skip it.
        ips = [ip for ip in _IPV4.findall(out) if ip != DNS_FALLBACK_SERVER]
        if not ips:
            raise TelegramError("DNS fallback could not resolve api.telegram.org")
        self._resolved_ip = ips[0]
        return ips[0]

    def _call_curl(self, method: str, payload: dict[str, Any]) -> Any:
        curl = shutil.which("curl.exe") or shutil.which("curl")
        if not curl:
            raise TelegramError("curl not found for the DNS fallback")
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
            raise TelegramError(f"{method}: curl failed: {type(exc).__name__}") from None
        if proc.returncode != 0:
            raise TelegramError(f"{method}: curl exit {proc.returncode}")
        body, _, code = (proc.stdout or "").rpartition("\n")
        return self._parse(method, int(code) if code.strip().isdigit() else 0, body)

    # ---- sending -------------------------------------------------------------

    def _send_chunk(self, text: str, markup: dict[str, Any] | None, silent: bool) -> int:
        payload: dict[str, Any] = {
            "chat_id": self.settings.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": silent,
        }
        if markup:
            payload["reply_markup"] = markup
        last: TelegramError | None = None
        for attempt in (1, 2):
            try:
                result = self.call("sendMessage", payload)
                return int(result.get("message_id", 0)) if isinstance(result, dict) else 0
            except TelegramError as exc:
                last = exc
                if "can't parse entities" in str(exc) and payload.get("parse_mode"):
                    log.warning("Telegram rejected the HTML; resending as plain text")
                    payload.pop("parse_mode")
                    payload["text"] = re.sub(r"<[^>]+>", "", text)
                    continue
                if attempt == 1:
                    log.warning("Telegram send failed (%s); retrying in %ss", exc, RETRY_DELAY_SECONDS)
                    self.sleep(RETRY_DELAY_SECONDS)
        raise last or TelegramError("send failed")

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
    ) -> int | None:
        """Send an HTML message. Returns the message_id, or None if it was not delivered."""
        if self.dry_run:
            print(f"[dry-run] telegram {kind}:\n{text}\n")
            return None
        if not self.configured:
            log.warning("Telegram not configured; %s not sent", kind)
            if preserve:
                self.preserve(text, buttons, kind)
            return None
        try:
            return self._deliver(text, buttons, silent)
        except TelegramError as exc:
            log.error("Telegram %s not delivered: %s", kind, exc)
            if preserve:
                path = self.preserve(getattr(exc, "unsent", text), buttons, kind)
                log.info("Saved undelivered %s to %s", kind, path)
            return None

    # ---- failures --------------------------------------------------------------

    def preserve(self, text: str, buttons: Buttons | None, kind: str) -> Path:
        folder = self.settings.delivery_failures_dir
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-z0-9_-]+", "-", kind.lower()).strip("-") or "message"
        stamp = datetime.now(self.settings.zoneinfo()).strftime("%Y%m%d_%H%M%S_%f")
        path = folder / f"{stamp}_{safe}.txt"
        meta = {"kind": kind, "buttons": [list(map(list, row)) for row in buttons] if buttons else None}
        path.write_text(META_PREFIX + json.dumps(meta) + "\n\n" + text, encoding="utf-8")
        return path

    @staticmethod
    def read_failure(path: Path) -> tuple[str, Buttons | None, str]:
        raw = path.read_text(encoding="utf-8")
        kind = path.stem.split("_", 3)[-1] if "_" in path.stem else "message"
        if raw.startswith(META_PREFIX):
            header, _, text = raw.partition("\n\n")
            try:
                meta = json.loads(header[len(META_PREFIX):])
            except json.JSONDecodeError:
                return raw, None, kind
            buttons = [[(t, d) for t, d in row] for row in meta.get("buttons") or []] or None
            return text, buttons, meta.get("kind") or kind
        return raw, None, kind

    def resend_failures(self) -> tuple[int, int]:
        """Resend preserved messages, oldest first, deleting each once sent. Returns (sent, left)."""
        folder = self.settings.delivery_failures_dir
        files = sorted(folder.glob("*.txt")) if folder.exists() else []
        if not files:
            return 0, 0
        if self.dry_run:
            print(f"[dry-run] would resend {len(files)} preserved message(s)")
            return 0, len(files)
        if not self.configured:
            return 0, len(files)
        sent = 0
        for path in files:
            text, buttons, kind = self.read_failure(path)
            try:
                self._deliver(text, buttons, silent=False)
            except TelegramError as exc:
                log.warning("Resend of %s failed (%s); keeping it for the next run", path.name, exc)
                break
            path.unlink()
            sent += 1
        if sent:
            log.info("Resent %d preserved message(s)", sent)
        return sent, len(files) - sent
