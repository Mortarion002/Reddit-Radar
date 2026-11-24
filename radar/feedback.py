"""Telegram feedback: button taps update lead status; /stats, /pause, /resume commands."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .digest import status_buttons
from .pipeline import Context
from .stats import compute_stats, format_stats
from .telegram import TelegramError

log = logging.getLogger("radar.feedback")

OFFSET_KEY = "last_update_id"
_CALLBACK_RE = re.compile(r"^s:(replied|skipped|not_relevant):([0-9a-f]{6,16})$")
DRAFT_MARKER = "Mentions Elvan:"


@dataclass
class FeedbackResult:
    updates: int = 0
    statuses: list[tuple[str, str]] = field(default_factory=list)  # (mention_id, status)
    commands: list[str] = field(default_factory=list)
    ignored: int = 0


def _chat_id(obj: dict[str, Any]) -> str:
    return str(((obj or {}).get("chat") or {}).get("id", ""))


def handle_callback(ctx: Context, cq: dict[str, Any], result: FeedbackResult) -> None:
    message = cq.get("message") or {}
    chat_id = _chat_id(message)
    if chat_id != str(ctx.settings.telegram_chat_id):
        log.warning("Ignoring a button tap from another chat")
        result.ignored += 1
        return
    callback_id = str(cq.get("id", ""))
    match = _CALLBACK_RE.match(str(cq.get("data", "")))
    if not match:
        log.warning("Unrecognised callback data %r", cq.get("data"))
        ctx.telegram.answer_callback(callback_id, "Unknown button")
        return
    status, short = match.groups()
    mention_id = ctx.store.resolve_short_id(short)
    if mention_id is None:
        ctx.telegram.answer_callback(callback_id, "Lead not found")
        return
    ctx.store.set_status(mention_id, status, ctx.now)
    message_id = message.get("message_id")
    if isinstance(message_id, int):
        changed = ctx.store.set_draft_posted(message_id, status == "replied")
        if not changed and DRAFT_MARKER in str(message.get("text", "")):
            ctx.store.set_latest_draft_posted(mention_id, status == "replied")
    result.statuses.append((mention_id, status))
    log.info("%s marked %s", mention_id, status)
    ctx.telegram.answer_callback(callback_id, f"Marked {status.replace('_', ' ')}")
    if isinstance(message_id, int):
        ctx.telegram.edit_buttons(chat_id, message_id, status_buttons(short, chosen=status))


def handle_message(ctx: Context, message: dict[str, Any], result: FeedbackResult) -> None:
    if _chat_id(message) != str(ctx.settings.telegram_chat_id):
        result.ignored += 1
        return
    text = str(message.get("text", "")).strip()
    if not text.startswith("/"):
        return
    command = text.split()[0].split("@")[0].lower()
    if command == "/stats":
        ctx.telegram.send(format_stats(compute_stats(ctx.store, 7, ctx.now)), kind="stats")
    elif command == "/pause":
        ctx.store.set_state("paused", "1")
        ctx.telegram.send("Paused. Scans will skip until you send /resume.", kind="reply")
    elif command == "/resume":
        ctx.store.set_state("paused", "0")
        ctx.telegram.send("Resumed. The next scheduled scan will run.", kind="reply")
    else:
        return
    result.commands.append(command)
    log.info("Telegram command %s", command)


def run_feedback(ctx: Context) -> FeedbackResult:
    result = FeedbackResult()
    last = int(ctx.store.get_state(OFFSET_KEY, "0") or 0)
    try:
        updates = ctx.telegram.get_updates(last + 1 if last else None)
    except TelegramError as exc:
        log.error("Could not read Telegram updates: %s", exc)
        return result
    for update in sorted(updates, key=lambda u: int(u.get("update_id", 0))):
        update_id = int(update.get("update_id", 0))
        if update_id <= last:
            continue  # replayed update: already handled
        try:
            if "callback_query" in update:
                handle_callback(ctx, update["callback_query"], result)
            elif "message" in update:
                handle_message(ctx, update["message"], result)
        except Exception:  # one bad update must not block the rest
            log.exception("Failed to handle Telegram update %s", update_id)
        last = update_id
        ctx.store.set_state(OFFSET_KEY, str(update_id))
        result.updates += 1
    return result
