"""Reply drafts written by Claude Code (/draft-replies): validate, store and send to Telegram."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from .digest import esc, lead_header, status_buttons
from .models import Draft, Lead, short_id
from .pipeline import Context, resend_pending
from .scorer import contains, normalize
from .store import Store

log = logging.getLogger("radar.drafts")

MIN_WORDS = 30
MAX_WORDS = 180
MENTION_RATIO = 0.2  # at most 1 in 5 replies names Elvan
RATIO_WINDOW_DAYS = 7
SENT_FILES_KEY = "sent_draft_files"

_DASHES = re.compile("[\u2014\u2013]")  # em dash, en dash
_ARROWS = re.compile("[\u2190-\u21ff\u27f0-\u27ff\u2900-\u297f]")
_EMOJI = re.compile(
    "[\U0001f000-\U0001faff\U0001f1e6-\U0001f1ff\u2600-\u27bf\u2b00-\u2bff\u3030\u303d\u3297\u3299\ufe0f\u200d]"
)
_HASHTAG = re.compile(r"(?<![\w&/#])#[A-Za-z][\w-]*")
_DISCLOSURE = re.compile(r"\bi(?:['\u2019]m| am) on the elvan team\b", re.IGNORECASE)
_ELVAN = re.compile(r"\belvan\b", re.IGNORECASE)
_PRICE = re.compile(r"\$\s?\d")
_URLS = re.compile(
    r"(https?://\S+|www\.\S+|\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:com|io|ai|co|net|org|app|dev|so|me|us|in|ly)\b(?:/\S*)?)",
    re.IGNORECASE,
)
_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")


class DraftsFileError(ValueError):
    pass


@dataclass
class DraftsFile:
    path: Path
    source_leads_file: str
    drafts: list[Draft] = field(default_factory=list)


# ---- loading --------------------------------------------------------------------------------


def load_drafts(path: Path) -> DraftsFile:
    """Load and shape-check a drafts file (spec section 10). Raises DraftsFileError with a clear message."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DraftsFileError(f"{path}: file not found") from None
    except json.JSONDecodeError as exc:
        raise DraftsFileError(f"{path}: not valid JSON ({exc.msg} at line {exc.lineno})") from None
    if not isinstance(data, dict) or not isinstance(data.get("drafts"), list):
        raise DraftsFileError(f'{path}: expected an object with a "drafts" list')
    drafts: list[Draft] = []
    for index, item in enumerate(data["drafts"]):
        where = f"{path}: drafts[{index}]"
        if not isinstance(item, dict):
            raise DraftsFileError(f"{where} is not an object")
        mid = item.get("mention_id")
        if not isinstance(mid, str) or not mid.startswith(("post:", "comment:")):
            raise DraftsFileError(f'{where}: "mention_id" must look like "post:<id>" or "comment:<id>"')
        if not isinstance(item.get("mentions_elvan"), bool):
            raise DraftsFileError(f'{where}: "mentions_elvan" must be true or false')
        if not isinstance(item.get("draft", ""), str) or not isinstance(item.get("notes", ""), str):
            raise DraftsFileError(f'{where}: "draft" and "notes" must be strings')
        drafts.append(Draft(mid, item["mentions_elvan"], item.get("draft", "") or "", item.get("notes", "") or ""))
    return DraftsFile(Path(path), str(data.get("source_leads_file", "")), drafts)


# ---- validation -------------------------------------------------------------------------------


def word_count(text: str) -> int:
    return len(text.split())


def _other_urls(text: str) -> list[str]:
    return [u for u in _URLS.findall(text) if u.rstrip(".,;:!?)").lower() != "elvan.ai"]


def validate(draft: Draft, competitors: Iterable[str] = ()) -> list[str]:
    """Problems with a draft against the reply rules. Empty list means it's fine."""
    text = draft.draft
    issues: list[str] = []
    if _DASHES.search(text):
        issues.append("em/en dash")
    if _EMOJI.search(text):
        issues.append("emoji")
    if _ARROWS.search(text):
        issues.append('unicode arrow (write "->")')
    if _HASHTAG.search(text):
        issues.append("hashtag")
    words = word_count(text)
    if words > MAX_WORDS:
        issues.append(f"too long ({words} words, max {MAX_WORDS})")
    elif words < MIN_WORDS:
        issues.append(f"too short ({words} words, min {MIN_WORDS})")
    if draft.mentions_elvan and not _DISCLOSURE.search(text):
        issues.append('mentions Elvan without "I\'m on the Elvan team"')
    if not draft.mentions_elvan and _ELVAN.search(text):
        issues.append("names Elvan but mentions_elvan is false")
    comp = list(competitors)
    for sentence in _SENTENCES.split(text):
        if _PRICE.search(sentence) and any(contains(normalize(sentence), c) for c in comp):
            issues.append("competitor price")
            break
    if _other_urls(text):
        issues.append('link other than plain "elvan.ai"')
    return issues


# ---- mention ratio --------------------------------------------------------------------------------


def allowed_mentions(mentions: int, replies: int, batch: int) -> int:
    """How many drafts in a batch may name Elvan so (mentions + k) / (replies + batch) <= 20%."""
    if batch <= 0:
        return 0
    return max(0, min(batch, (replies + batch) // 5 - mentions))


def ratio_ok(store: Store, new_mentions: int, batch: int, now: datetime) -> bool:
    """True if `new_mentions` Elvan mentions in a batch of `batch` drafts keeps the 7-day ratio at or under 20%."""
    replies, mentions = store.reply_stats(now - timedelta(days=RATIO_WINDOW_DAYS))
    return new_mentions <= allowed_mentions(mentions, replies, batch)


# ---- sending -------------------------------------------------------------------------------------


def sent_files(store: Store) -> list[str]:
    try:
        return list(json.loads(store.get_state(SENT_FILES_KEY) or "[]"))
    except json.JSONDecodeError:
        return []


def mark_file_sent(store: Store, path: Path) -> None:
    names = sent_files(store)
    if path.name not in names:
        names.append(path.name)
    store.set_state(SENT_FILES_KEY, json.dumps(names[-500:]))


def unsent_files(folder: Path, store: Store) -> list[Path]:
    """Drafts files not sent yet, oldest first."""
    if not folder.exists():
        return []
    done = set(sent_files(store))
    return sorted((p for p in folder.glob("*.json") if p.name not in done), key=lambda p: p.name)


def newest_unsent(folder: Path, store: Store) -> Path | None:
    files = unsent_files(folder, store)
    return files[-1] if files else None


def format_draft_message(lead: Lead | None, mention_id: str, draft: Draft, issues: list[str], now: datetime) -> str:
    lines: list[str] = []
    if issues:
        lines.append(f"<b>NEEDS EDIT: {esc('; '.join(issues))}</b>")
    if lead is not None:
        lines.append(f"<b>{esc(lead_header(lead, now))}</b>")
        lines.append(f"Thread: {esc(lead.mention.title)}")
        lines.append(esc(lead.mention.url))
    else:
        lines.append(f"<b>DRAFT | {esc(mention_id)}</b> (lead not in the database)")
    lines.append("")
    lines.append(f"<pre>{esc(draft.draft.strip())}</pre>")
    lines.append(f"Mentions Elvan: {'yes' if draft.mentions_elvan else 'no'}")
    if draft.notes.strip():
        lines.append(f"Notes: {esc(draft.notes.strip())}")
    return "\n".join(lines)


@dataclass
class SendResult:
    path: Path | None  # the last file handled
    sent: int = 0  # drafts delivered to Telegram now
    queued: int = 0  # drafts that couldn't be delivered; saved and resent on the next run
    flagged: int = 0
    skipped_empty: int = 0
    duplicates: int = 0
    already_sent: bool = False
    resent: int = 0  # earlier undelivered messages that went out at the start of this run
    files: list[Path] = field(default_factory=list)
    stale: list[Path] = field(default_factory=list)


def competitor_names(ctx: Context) -> list[str]:
    comps = ctx.settings.signals.get("competitors") or {}
    return [str(c) for c in (comps.get("priority") or []) + (comps.get("tracked") or [])]


def _send_file(ctx: Context, target: Path, result: SendResult) -> None:
    drafts_file = load_drafts(target)
    real = [d for d in drafts_file.drafts if d.draft.strip()]
    replies, mentions = ctx.store.reply_stats(ctx.now - timedelta(days=RATIO_WINDOW_DAYS))
    allowed = allowed_mentions(mentions, replies, len(real))
    used = 0
    competitors = competitor_names(ctx)

    for draft in drafts_file.drafts:
        if not draft.draft.strip():
            result.skipped_empty += 1
            log.info("Skipped lead %s: %s", draft.mention_id, draft.notes or "(no reason given)")
            continue
        if ctx.store.find_draft(draft.mention_id, draft.draft) is not None:
            # Already saved by an earlier, interrupted run; it was sent or is queued for resend.
            result.duplicates += 1
            log.info("Draft for %s already handled; not sending it twice", draft.mention_id)
            continue
        issues = validate(draft, competitors)
        if draft.mentions_elvan:
            if used >= allowed:
                issues.append("over mention ratio, remove Elvan")
            else:
                used += 1
        draft.issues = issues
        lead = ctx.store.get_lead(draft.mention_id)
        text = format_draft_message(lead, draft.mention_id, draft, issues, ctx.now)
        # Save first (not yet delivered), then send. If the send fails, the message is queued in
        # delivery_failures/ with this draft id, and resend_pending records the delivery later.
        draft_id = ctx.store.save_draft(
            draft.mention_id, draft.mentions_elvan, draft.draft, draft.notes, ctx.now, None, None
        )
        message_id = ctx.telegram.send(
            text,
            status_buttons(short_id(draft.mention_id)),
            kind="draft",
            meta={"draft_id": draft_id, "mention_id": draft.mention_id},
        )
        if message_id is not None:
            ctx.store.mark_draft_delivered(draft_id, message_id, ctx.now)
            result.sent += 1
        elif ctx.dry_run:
            result.sent += 1  # printed instead of sent
        else:
            result.queued += 1
        result.flagged += bool(issues)
    mark_file_sent(ctx.store, target)


def send_drafts(ctx: Context, path: Path | None = None) -> SendResult:
    """Send drafts to Telegram.

    First resends anything still waiting in delivery_failures/ (e.g. drafts whose send failed last
    time). Then, with a path, sends that file; without one, sends every unsent drafts file, oldest
    first. Files older than MAX_LEAD_AGE_HOURS are marked done without sending (their leads expired).
    """
    result = SendResult(None)
    result.resent, _ = resend_pending(ctx)

    if path is not None:
        result.path = path
        if path.name in sent_files(ctx.store):
            log.info("%s was already sent; skipping", path.name)
            result.already_sent = True
            return result
        _send_file(ctx, path, result)
        result.files.append(path)
        return result

    cutoff = (ctx.now - timedelta(hours=ctx.settings.max_lead_age_hours)).timestamp()
    for target in unsent_files(ctx.settings.drafts_dir, ctx.store):
        result.path = target
        if target.stat().st_mtime < cutoff:
            log.warning("%s is older than %sh; its leads expired, not sending", target.name, ctx.settings.max_lead_age_hours)
            mark_file_sent(ctx.store, target)
            result.stale.append(target)
            continue
        _send_file(ctx, target, result)
        result.files.append(target)
    if result.path is None:
        log.info("No unsent drafts file in %s", ctx.settings.drafts_dir)
    return result
