"""Records: Mention, Lead, Draft, plus Reddit URL parsing (parsing only, never fetching)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

REDDIT_HOSTS = {
    "reddit.com",
    "www.reddit.com",
    "old.reddit.com",
    "np.reddit.com",
    "new.reddit.com",
    "m.reddit.com",
    "i.reddit.com",
}
# Reddit ids are base36. Posts are ~7 chars today; newer comment ids are 13 (e.g. "19t5tnte5nfsw").
_ID_RE = re.compile(r"^[a-z0-9]{2,20}$")
# Path segments that stand in for the slug before a comment id: F5Bot links use
# /comments/{post_id}/c/{comment_id}, Reddit's share links use .../comment/{comment_id}.
_COMMENT_MARKERS = {"c", "comment"}


def parse_reddit_url(url: str) -> tuple[str, str, str | None]:
    """Return (subreddit, post_id, comment_id or None) for a Reddit permalink.

    Handles /r/{sub}/comments/{post_id}/{slug}/ and .../{slug}/{comment_id}/,
    the .../comments/{post_id}/c/{comment_id} form F5Bot links to, the
    .../comments/{post_id}/comment/{comment_id}/ share form, old./np. hosts,
    trailing slashes and query strings (e.g. ?context=3). Raises ValueError for anything else.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("empty URL")
    parts = urlsplit(url.strip())  # query string and fragment are dropped here
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"not an http(s) URL: {url!r}")
    host = (parts.hostname or "").lower()
    if host not in REDDIT_HOSTS:
        raise ValueError(f"not a Reddit URL: {url!r}")
    segs = [s for s in parts.path.split("/") if s]
    if len(segs) < 4 or segs[0].lower() != "r" or segs[2].lower() != "comments":
        raise ValueError(f"not a Reddit post or comment permalink: {url!r}")
    subreddit, post_id = segs[1], segs[3].lower()
    if not _ID_RE.match(post_id):
        raise ValueError(f"bad Reddit post id in {url!r}")
    comment_id: str | None = None
    rest = segs[4:]
    if len(rest) >= 2:  # {slug}/{comment_id}, c/{comment_id} or comment/{comment_id}
        comment_id = rest[1].lower()
        if not _ID_RE.match(comment_id):
            raise ValueError(f"bad Reddit comment id in {url!r}")
    return subreddit, post_id, comment_id


def canonical_url(subreddit: str, post_id: str, comment_id: str | None, slug: str = "_") -> str:
    base = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/{slug}/"
    return base + (f"{comment_id}/" if comment_id else "")


def _slug_from(url: str) -> str:
    segs = [s for s in urlsplit(url).path.split("/") if s]
    if len(segs) >= 5 and segs[4].lower() not in _COMMENT_MARKERS:
        return segs[4]
    return "_"


def short_id(mention_id: str) -> str:
    """Short stable hash of a mention_id, used in Telegram callback data."""
    return hashlib.sha1(mention_id.encode("utf-8")).hexdigest()[:10]


@dataclass(slots=True)
class Mention:
    mention_id: str  # "post:<post_id>" or "comment:<comment_id>"
    kind: str  # "post" | "comment"
    post_id: str
    subreddit: str
    title: str
    snippet: str
    url: str
    author: str | None
    keyword: str
    received_at: datetime  # timezone-aware
    source: str = "f5bot"

    @property
    def text(self) -> str:
        return "\n".join(p for p in (self.title.strip(), self.snippet.strip()) if p)

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        title: str,
        snippet: str,
        keyword: str,
        received_at: datetime,
        author: str | None = None,
        source: str = "f5bot",
    ) -> "Mention":
        if received_at.tzinfo is None:
            raise ValueError("received_at must be timezone-aware")
        subreddit, post_id, comment_id = parse_reddit_url(url)
        kind = "comment" if comment_id else "post"
        mention_id = f"comment:{comment_id}" if comment_id else f"post:{post_id}"
        return cls(
            mention_id=mention_id,
            kind=kind,
            post_id=post_id,
            subreddit=subreddit,
            title=title.strip(),
            snippet=snippet.strip(),
            url=canonical_url(subreddit, post_id, comment_id, _slug_from(url)),
            author=author,
            keyword=keyword.strip(),
            received_at=received_at,
            source=source,
        )


@dataclass(slots=True)
class Lead:
    mention: Mention
    primary_signal: str
    signals: list[str]
    matched_terms: list[str]
    competitor: str | None
    score: float
    tier: str  # High | Medium | Low | Dropped
    scored_at: datetime
    drop_reason: str | None = None
    alerted_at: datetime | None = None
    digested_at: datetime | None = None
    exported_at: datetime | None = None
    status: str = "new"  # new | replied | skipped | not_relevant
    status_at: datetime | None = None

    @property
    def mention_id(self) -> str:
        return self.mention.mention_id

    @property
    def short_id(self) -> str:
        return short_id(self.mention.mention_id)


@dataclass(slots=True)
class Draft:
    mention_id: str
    mentions_elvan: bool
    draft: str
    notes: str = ""
    issues: list[str] = field(default_factory=list)
