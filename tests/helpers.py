"""Test helpers for building mentions."""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

from radar.models import Mention

NOW = datetime(2026, 11, 25, 6, 0, tzinfo=timezone.utc)
_ids = itertools.count(1)


def _b36(n: int) -> str:
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = chars[r] + out
    return out.rjust(6, "a")


def mention(
    title: str = "",
    snippet: str = "",
    *,
    sub: str = "SaaS",
    kind: str = "post",
    hours: float = 1.0,
    keyword: str = "nps tool",
    post_id: str | None = None,
    now: datetime = NOW,
) -> Mention:
    pid = post_id or "p" + _b36(next(_ids))
    url = f"https://www.reddit.com/r/{sub}/comments/{pid}/slug/"
    if kind == "comment":
        url += "c" + _b36(next(_ids)) + "/"
    return Mention.from_url(
        url, title=title, snippet=snippet, keyword=keyword, received_at=now - timedelta(hours=hours)
    )
