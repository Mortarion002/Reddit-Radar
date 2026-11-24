"""F5Bot alert email -> list[Mention]. Stdlib only (email + html.parser).

The parser does not depend on exact F5Bot markup. It walks the email's links in order:
- a link whose (unwrapped) target is a Reddit post/comment permalink starts a Reddit match;
  its link text is the thread title and the text after it, up to the next link, is the
  snippet (plus an optional "Keyword:" line);
- a link to Hacker News or Lobsters starts a non-Reddit match, which is skipped;
- any other link (settings, unsubscribe) ends the current match.

NOTE: built before real F5Bot emails were available (see docs/PROGRESS.md, stage 4).
Re-check against real `.eml` files in tests/fixtures/f5bot/.
"""

from __future__ import annotations

import email
import email.policy
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlsplit

from ..models import Mention, parse_reddit_url

log = logging.getLogger("radar.f5bot")

NON_REDDIT_HOSTS = ("news.ycombinator.com", "lobste.rs", "www.lobste.rs")
REDIRECT_PARAMS = ("u", "url", "q", "target", "redirect", "link", "to", "dest")
_BLOCK_TAGS = {
    "br", "p", "div", "li", "tr", "td", "blockquote", "ul", "ol", "table", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6", "pre",
}
_SKIP_TAGS = {"style", "script", "head", "title"}
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_KEYWORD_RE = re.compile(r"^\s*keywords?\s*:\s*(.+?)\s*$", re.IGNORECASE)
_AUTHOR_RE = re.compile(r"(?:^|\s)(?:by|author:?)\s+/?u/([A-Za-z0-9_-]{3,20})\b", re.IGNORECASE)
# Section headers such as "Reddit Comments (/r/SaaS/):" or "Hacker News Posts:"
_SECTION_RE = re.compile(
    r"^\s*(reddit|hacker\s*news|lobste\.?rs)\s+(posts?|comments?|stories|story)\b[^:]{0,60}:\s*$", re.IGNORECASE
)
_FOOTER_RE = re.compile(
    r"(f5bot|unsubscribe|you are receiving|you're receiving|manage your (alerts|keywords)|email preferences)",
    re.IGNORECASE,
)
_SUBJECT_KEYWORD_RE = re.compile(r"f5bot\s+found\s+something\s*[:!-]*\s*(.*)$", re.IGNORECASE)


class ParseError(ValueError):
    pass


@dataclass(slots=True)
class _Match:
    url: str
    title: str
    reddit: bool
    lines: list[str] = field(default_factory=list)


# ---- link handling ------------------------------------------------------------------


def unwrap_url(url: str, depth: int = 0) -> str:
    """Follow redirect wrappers like https://f5bot.com/url?u=<encoded> to the real target."""
    url = url.strip()
    if depth > 4 or not url.lower().startswith(("http://", "https://")):
        return url
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.endswith("reddit.com"):
        return url
    query = parse_qs(parts.query)
    for key in REDIRECT_PARAMS:
        for value in query.get(key, []):
            candidate = unquote(value) if "%3a" in value.lower() else value
            if candidate.lower().startswith(("http://", "https://")):
                return unwrap_url(candidate, depth + 1)
    return url


def _is_reddit_permalink(url: str) -> bool:
    try:
        parse_reddit_url(url)
        return True
    except ValueError:
        return False


def _is_non_reddit_match(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in NON_REDDIT_HOSTS


# ---- HTML -> token stream -------------------------------------------------------------


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[tuple[str, str, str]] = []  # ("text", data, "") | ("link", href, text)
        self._href: str | None = None
        self._anchor: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "a":
            self._close_link()
            self._href = dict(attrs).get("href") or ""
            self._anchor = []
        elif tag in _BLOCK_TAGS:
            self._emit("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS:
            self._emit("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag == "a":
            self._close_link()
        elif tag in _BLOCK_TAGS:
            self._emit("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._href is not None:
            self._anchor.append(data)
        else:
            self._emit(data)

    def _emit(self, text: str) -> None:
        self.tokens.append(("text", text, ""))

    def _close_link(self) -> None:
        if self._href is not None:
            self.tokens.append(("link", self._href, " ".join("".join(self._anchor).split())))
            self._href = None
            self._anchor = []

    def close(self) -> None:
        super().close()
        self._close_link()


def _html_tokens(html_text: str) -> list[tuple[str, str, str]]:
    collector = _Collector()
    collector.feed(html_text)
    collector.close()
    return collector.tokens


def _text_tokens(plain: str) -> list[tuple[str, str, str]]:
    """Plain text: a URL becomes a link whose title is the nearest non-empty line above it."""
    tokens: list[tuple[str, str, str]] = []
    pending: list[str] = []
    for line in plain.splitlines():
        urls = _URL_RE.findall(line)
        if not urls:
            pending.append(line)
            continue
        before = [l for l in pending if l.strip()]
        title = ""
        if before and not _KEYWORD_RE.match(before[-1]) and not _SECTION_RE.match(before[-1]):
            title = before.pop().strip()
        tokens.append(("text", "\n".join(before) + "\n", ""))
        pending = []
        tokens.append(("link", urls[0], title))
        rest = line.replace(urls[0], "").strip()
        if rest:
            tokens.append(("text", rest + "\n", ""))
    tokens.append(("text", "\n".join(pending), ""))
    return tokens


# ---- tokens -> matches ----------------------------------------------------------------


def _matches(tokens: list[tuple[str, str, str]]) -> tuple[list[_Match], int, int]:
    """Return (matches, non-Reddit matches skipped, total http links)."""
    matches: list[_Match] = []
    current: _Match | None = None
    skipped = links = 0
    buffer: list[str] = []

    def flush() -> None:
        if current is not None and buffer:
            current.lines.extend("".join(buffer).splitlines())
        buffer.clear()

    for kind, value, text in tokens:
        if kind == "text":
            buffer.append(value)
            continue
        href = unwrap_url(value)
        if href.lower().startswith(("http://", "https://")):
            links += 1
        flush()
        if _is_reddit_permalink(href):
            if current is not None and current.reddit and current.url == href and not current.lines:
                continue  # same link twice in a row (title link + "view" link)
            current = _Match(url=href, title=text, reddit=True)
            matches.append(current)
        elif _is_non_reddit_match(href):
            log.debug("Skipping non-Reddit match: %s", href)
            skipped += 1
            current = _Match(url=href, title=text, reddit=False)
        else:
            current = None
    flush()
    return [m for m in matches if m.reddit], skipped, links


def _clean_lines(lines: list[str]) -> tuple[list[str], str | None, str | None]:
    keyword = author = None
    kept: list[str] = []
    for raw in lines:
        line = " ".join(raw.split())
        if not line or re.fullmatch(r"[-_=*]{2,}", line):  # blank or a separator like "--"
            continue
        kw = _KEYWORD_RE.match(line)
        if kw:
            keyword = keyword or kw.group(1).strip().strip("\"'")
            continue
        if _SECTION_RE.match(line) or _FOOTER_RE.search(line):
            continue
        found = _AUTHOR_RE.search(line)
        if found and len(line) < 60:
            author = author or found.group(1)
            continue
        kept.append(line)
    return kept, keyword, author


def _clean_title(title: str, url: str) -> str:
    title = " ".join(title.split())
    if title.lower().startswith(("http://", "https://")) or title == url:
        return ""
    return re.sub(r"^(re|comment on)\s*:\s*", "", title, flags=re.IGNORECASE).strip()


# ---- email ---------------------------------------------------------------------------------


def _bodies(msg: EmailMessage) -> tuple[str | None, str | None]:
    html_part = msg.get_body(preferencelist=("html",))
    text_part = msg.get_body(preferencelist=("plain",))
    html_body = html_part.get_content() if html_part is not None else None
    text_body = text_part.get_content() if text_part is not None else None
    return html_body, text_body


def received_at(msg: EmailMessage) -> datetime:
    value = msg.get("Date")
    if value:
        try:
            dt = parsedate_to_datetime(str(value))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            log.warning("Unparseable Date header %r; using now", value)
    return datetime.now(timezone.utc)


def subject_keyword(msg: EmailMessage) -> str:
    subject = " ".join(str(msg.get("Subject", "")).split())
    match = _SUBJECT_KEYWORD_RE.search(subject)
    return match.group(1).strip().strip("\"'") if match else ""


def parse_message(raw: bytes) -> EmailMessage:
    return email.message_from_bytes(raw, policy=email.policy.default)  # type: ignore[return-value]


def parse_email(raw: bytes) -> list[Mention]:
    """Every Reddit match in an F5Bot alert email. Raises ParseError if links exist but none parse."""
    try:
        msg = parse_message(raw)
        html_body, text_body = _bodies(msg)
    except Exception as exc:  # malformed MIME
        raise ParseError(f"unreadable email: {exc}") from exc
    if html_body is None and text_body is None:
        raise ParseError("email has no text or HTML body")

    tokens = _html_tokens(html_body) if html_body is not None else _text_tokens(text_body or "")
    found, skipped, links = _matches(tokens)
    if not found and html_body is not None and text_body:
        found, skipped2, links2 = _matches(_text_tokens(text_body))
        skipped, links = max(skipped, skipped2), max(links, links2)

    when = received_at(msg)
    default_keyword = subject_keyword(msg)
    mentions: list[Mention] = []
    seen: set[str] = set()
    for match in found:
        lines, keyword, author = _clean_lines(match.lines)
        try:
            mention = Mention.from_url(
                match.url,
                title=_clean_title(match.title, match.url),
                snippet=" ".join(lines),
                keyword=keyword or default_keyword,
                received_at=when,
                author=author,
                source="f5bot",
            )
        except ValueError as exc:
            log.debug("Skipping unusable match %s: %s", match.url, exc)
            continue
        if mention.mention_id not in seen:
            seen.add(mention.mention_id)
            mentions.append(mention)

    if not mentions and not skipped:
        # Every F5Bot alert has links. No match and no recognised HN/Lobsters item means the
        # format changed or this isn't an alert: keep the email in data/unparsed/ for a human.
        if links:
            raise ParseError(f"{links} link(s) but no Reddit match could be parsed")
        raise ParseError("no links found in the email")
    return mentions


def message_id(raw: bytes) -> str:
    try:
        return str(parse_message(raw).get("Message-ID", "") or "")
    except Exception:
        return ""
