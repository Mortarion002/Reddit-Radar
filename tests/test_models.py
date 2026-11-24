from __future__ import annotations

from datetime import datetime, timezone

import pytest

from radar.models import Mention, parse_reddit_url, short_id

NOW = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)


def _mention(url: str, **kw) -> Mention:
    base = dict(title="t", snippet="s", keyword="k", received_at=NOW)
    base.update(kw)
    return Mention.from_url(url, **base)


def test_post_url():
    assert parse_reddit_url("https://www.reddit.com/r/SaaS/comments/abc123/some_title/") == ("SaaS", "abc123", None)


def test_comment_url():
    url = "https://www.reddit.com/r/CustomerSuccess/comments/abc123/some_title/def456/"
    assert parse_reddit_url(url) == ("CustomerSuccess", "abc123", "def456")


def test_new_style_comment_url():
    url = "https://www.reddit.com/r/SaaS/comments/abc123/comment/def456/"
    assert parse_reddit_url(url) == ("SaaS", "abc123", "def456")


def test_query_string_and_no_trailing_slash():
    url = "https://www.reddit.com/r/SaaS/comments/abc123/title/def456?utm_source=share&context=3"
    assert parse_reddit_url(url) == ("SaaS", "abc123", "def456")
    assert parse_reddit_url("https://reddit.com/r/SaaS/comments/abc123") == ("SaaS", "abc123", None)


@pytest.mark.parametrize("host", ["old.reddit.com", "np.reddit.com", "reddit.com", "new.reddit.com"])
def test_alt_hosts(host):
    assert parse_reddit_url(f"https://{host}/r/startups/comments/xyz789/t/") == ("startups", "xyz789", None)


@pytest.mark.parametrize(
    "url",
    [
        "https://news.ycombinator.com/item?id=1",
        "https://www.reddit.com/r/SaaS/",
        "https://www.reddit.com/user/someone/",
        "https://evilreddit.com/r/SaaS/comments/abc123/t/",
        "ftp://www.reddit.com/r/SaaS/comments/abc123/t/",
        "",
        "not a url",
    ],
)
def test_non_reddit_raises(url):
    with pytest.raises(ValueError):
        parse_reddit_url(url)


def test_from_url_post():
    m = _mention(
        "https://old.reddit.com/r/SaaS/comments/abc123/nps_tools/?ref=x",
        title="NPS tools?",
        snippet="Looking for one",
        keyword="nps tool",
    )
    assert m.mention_id == "post:abc123"
    assert m.kind == "post"
    assert m.post_id == "abc123"
    assert m.subreddit == "SaaS"
    assert m.url == "https://www.reddit.com/r/SaaS/comments/abc123/nps_tools/"
    assert m.source == "f5bot"


def test_from_url_comment():
    m = _mention("https://www.reddit.com/r/SaaS/comments/abc123/nps_tools/def456/", author="someone")
    assert m.mention_id == "comment:def456"
    assert m.kind == "comment"
    assert m.post_id == "abc123"
    assert m.author == "someone"
    assert m.url.endswith("/abc123/nps_tools/def456/")


def test_text_joins_title_and_snippet():
    m = _mention("https://www.reddit.com/r/SaaS/comments/abc123/t/", title="Title here", snippet="snippet here")
    assert m.text == "Title here\nsnippet here"


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        _mention("https://www.reddit.com/r/SaaS/comments/abc123/t/", received_at=datetime(2026, 1, 1))


def test_short_id_stable_and_short():
    assert short_id("comment:def456") == short_id("comment:def456")
    assert short_id("comment:def456") != short_id("comment:def457")
    assert len(short_id("comment:def456")) == 10
