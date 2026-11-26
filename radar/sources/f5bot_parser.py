"""F5Bot alert email -> list[Mention]. Stdlib only (email + html.parser).

Real F5Bot layout (from tests/fixtures/f5bot/*.eml, Nov 2026):
- From "F5Bot Alerts <alerts@f5bot.com>", subject "F5Bot found something: <keyword>"
- multipart/alternative; the HTML has an <h2>Keyword: "csat"</h2> heading per keyword,
  then one <p> per hit: "Reddit Posts (/r/UPSC/):" <a href="https://f5bot.com/url?u=<encoded
  reddit URL>&i=..&h=..">thread title</a> "by <author>" <br> <excerpt>
- post links unwrap to /r/{sub}/comments/{post_id}/{slug}/, comment links to
  /r/{sub}/comments/{post_id}/c/{comment_id}?context=3
- an ad block (f5bot.com/url?a=..) and footer links on f5bot.com

The parser walks the links in order rather than relying on exact markup:
- a link whose unwrapped target is a Reddit permalink starts a hit; its link text is the
  thread title, and the text up to the next link holds the author line and the excerpt;
- a "Keyword:" line is a heading for the hits after it (or, as the first line after a
  link, that hit's own keyword); the subject is the fallback;
- Hacker News / Lobsters links are skipped (debug log);
- a link that looks like a hit (an F5Bot redirect, or any reddit.com URL) but doesn't
  parse is never dropped silently: it's logged as a warning and returned in
  ParseResult.failed_hits so the run summary counts it;
- any other link (ad, settings, unsubscribe) ends the current hit.
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
# "by Spare-upsc-80" right after the hit link
_BY_LINE_RE = re.compile(r"^by\s+/?(?:u/)?([A-Za-z0-9_-]{3,30})$", re.IGNORECASE)
# F5Bot's own text after the last hit ("Do you have comments or suggestions about F5Bot?",
# the ad block, the footer). It ends the current hit, so ad copy never lands in a snippet.
_END_OF_HITS_RE = re.compile(
    r"(suggestions about f5bot|want to advertise|you are receiving this|you're receiving this)", re.IGNORECASE
)
# "Keyword was found in submission title." at the start of an excerpt
_FOUND_IN_RE = re.compile(r"^keyword was found in\b.*$", re.IGNORECASE)
# Plain text hit line: Reddit Posts (/r/UPSC/): 'Title here' by someone
_TEXT_HIT_RE = re.compile(
    r"^\s*(?:reddit|hacker\s*news|lobste\.?rs)\s+(?:posts?|comments?|stories)\b[^:]{0,60}:\s*'(.*)'\s+by\s+(\S+)\s*$",
    re.IGNORECASE,
)


class ParseError(ValueError):
    def __init__(self, message: str, failed_hits: list[str] | None = None) -> None:
        super().__init__(message)
        self.failed_hits = failed_hits or []


@dataclass(slots=True)
class _Match:
    url: str
    title: str
    reddit: bool
    group_keyword: str | None = None  # from the "Keyword:" heading above this hit
    keyword: str | None = None  # from a "Keyword:" line directly under the link
    lines: list[str] = field(default_factory=list)


@dataclass
class ParseResult:
    mentions: list[Mention] = field(default_factory=list)
    failed_hits: list[str] = field(default_factory=list)  # hit URLs that could not be parsed
    non_reddit: int = 0  # Hacker News / Lobsters hits skipped


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
        title, author = "", None
        if before:
            hit = _TEXT_HIT_RE.match(before[-1])
            if hit:
                title, author = hit.group(1).strip(), hit.group(2)
                before.pop()
            elif not _KEYWORD_RE.match(before[-1]) and not _SECTION_RE.match(before[-1]):
                title = before.pop().strip()
        tokens.append(("text", "\n".join(before) + "\n", ""))
        pending = []
        tokens.append(("link", urls[0], title))
        if author:
            tokens.append(("text", f"by {author}\n", ""))
        rest = line.replace(urls[0], "").strip()
        if rest:
            tokens.append(("text", rest + "\n", ""))
    tokens.append(("text", "\n".join(pending), ""))
    return tokens


# ---- tokens -> matches ----------------------------------------------------------------


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _looks_like_hit(raw: str, href: str) -> bool:
    """A link F5Bot uses for a hit: a redirect to a real URL, or any reddit.com link."""
    host = _host(href)
    redirected = href != raw.strip() and href.lower().startswith(("http://", "https://"))
    return host == "reddit.com" or host.endswith(".reddit.com") or (redirected and not host.endswith("f5bot.com"))


def _matches(tokens: list[tuple[str, str, str]]) -> tuple[list[_Match], int, int, list[str]]:
    """Return (Reddit hits, non-Reddit hits skipped, total http links, hit URLs that failed to parse)."""
    matches: list[_Match] = []
    failed: list[str] = []
    current: _Match | None = None
    group_keyword: str | None = None
    skipped = links = 0
    buffer: list[str] = []

    def flush() -> None:
        nonlocal group_keyword, current
        text = "".join(buffer)
        buffer.clear()
        for raw_line in text.splitlines():
            line = " ".join(raw_line.split())
            if not line:
                continue
            if _END_OF_HITS_RE.search(line):
                current = None
                continue
            kw = _KEYWORD_RE.match(line)
            if kw:
                value = kw.group(1).strip().strip("\"'")
                if current is not None and not current.lines and current.keyword is None:
                    current.keyword = value  # "Keyword:" directly under the link
                else:
                    group_keyword = value  # heading for the hits that follow
                continue
            if current is not None:
                current.lines.append(line)

    for kind, value, text in tokens:
        if kind == "text":
            buffer.append(value)
            continue
        flush()
        href = unwrap_url(value)
        if href.lower().startswith(("http://", "https://")):
            links += 1
        if _is_reddit_permalink(href):
            if current is not None and current.reddit and current.url == href and not current.lines:
                continue  # same link twice in a row (title link + "view" link)
            current = _Match(url=href, title=text, reddit=True, group_keyword=group_keyword)
            matches.append(current)
        elif _is_non_reddit_match(href):
            log.debug("Skipping non-Reddit match: %s", href)
            skipped += 1
            current = _Match(url=href, title=text, reddit=False)
        elif _looks_like_hit(value, href):
            log.warning("F5Bot hit skipped: could not parse the Reddit URL %s", href)
            failed.append(href)
            current = None
        else:
            current = None
    flush()
    return [m for m in matches if m.reddit], skipped, links, failed


def _clean_lines(lines: list[str]) -> tuple[list[str], str | None]:
    """Snippet lines and the author, with headers, footers and F5Bot boilerplate removed."""
    author = None
    kept: list[str] = []
    for index, raw in enumerate(lines):
        line = " ".join(raw.split())
        if not line or re.fullmatch(r"[-_=*]{2,}", line):  # blank or a separator like "--"
            continue
        by = _BY_LINE_RE.match(line)
        if by and index == 0:
            author = by.group(1)
            continue
        if _FOUND_IN_RE.match(line) or _SECTION_RE.match(line) or _FOOTER_RE.search(line):
            continue
        found = _AUTHOR_RE.search(line)
        if found and len(line) < 60:
            author = author or found.group(1)
            continue
        kept.append(line)
    return kept, author


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


def _build(found: list[_Match], when: datetime, default_keyword: str, failed: list[str]) -> list[Mention]:
    mentions: list[Mention] = []
    seen: set[str] = set()
    for match in found:
        lines, author = _clean_lines(match.lines)
        try:
            mention = Mention.from_url(
                match.url,
                title=_clean_title(match.title, match.url),
                snippet=" ".join(lines),
                keyword=match.keyword or match.group_keyword or default_keyword,
                received_at=when,
                author=author,
                source="f5bot",
            )
        except ValueError as exc:
            log.warning("F5Bot hit skipped: %s (%s)", match.url, exc)
            failed.append(match.url)
            continue
        if mention.mention_id not in seen:
            seen.add(mention.mention_id)
            mentions.append(mention)
    return mentions


def parse_email_result(raw: bytes) -> ParseResult:
    """Every Reddit hit in an F5Bot alert email, plus the hits that could not be parsed.

    Raises ParseError when the email has no usable Reddit hit and no HN/Lobsters hit.
    """
    try:
        msg = parse_message(raw)
        html_body, text_body = _bodies(msg)
    except Exception as exc:  # malformed MIME
        raise ParseError(f"unreadable email: {exc}") from exc
    if html_body is None and text_body is None:
        raise ParseError("email has no text or HTML body")

    when = received_at(msg)
    default_keyword = subject_keyword(msg)
    tokens = _html_tokens(html_body) if html_body is not None else _text_tokens(text_body or "")
    found, skipped, links, failed = _matches(tokens)
    mentions = _build(found, when, default_keyword, failed)
    if not mentions and html_body is not None and text_body:
        found2, skipped2, links2, failed2 = _matches(_text_tokens(text_body))
        mentions2 = _build(found2, when, default_keyword, failed2)
        if mentions2:
            mentions, failed = mentions2, failed2
        skipped, links = max(skipped, skipped2), max(links, links2)

    if not mentions and not skipped:
        # Every F5Bot alert has links. No hit and no recognised HN/Lobsters item means the
        # format changed or this isn't an alert: keep the email in data/unparsed/ for a human.
        if failed:
            raise ParseError(f"{len(failed)} hit(s) but none could be parsed: {', '.join(failed)}", failed)
        if links:
            raise ParseError(f"{links} link(s) but no Reddit match could be parsed")
        raise ParseError("no links found in the email")
    return ParseResult(mentions=mentions, failed_hits=failed, non_reddit=skipped)


def parse_email(raw: bytes) -> list[Mention]:
    """Every Reddit hit in an F5Bot alert email. Raises ParseError if there is none to use."""
    return parse_email_result(raw).mentions


def message_id(raw: bytes) -> str:
    try:
        return str(parse_message(raw).get("Message-ID", "") or "")
    except Exception:
        return ""
