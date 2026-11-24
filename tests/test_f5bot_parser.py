from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import pytest

from radar.sources.f5bot_parser import ParseError, parse_email, unwrap_url

from .conftest import FIXTURES

SYNTH = FIXTURES / "f5bot_synthetic"
REAL = FIXTURES / "f5bot"
REAL_FILES = sorted(REAL.glob("*.eml"))


def load(name: str) -> bytes:
    return (SYNTH / name).read_bytes()


# ---- synthetic fixtures (best-known F5Bot layout, see PROGRESS.md) --------------------


def test_single_post():
    [m] = parse_email(load("single_post.eml"))
    assert m.mention_id == "post:1q2w3e"
    assert m.kind == "post"
    assert m.subreddit == "CustomerSuccess"
    assert m.title == "Survicate alternatives for a small CS team?"
    assert m.keyword == "survicate"
    assert "Survicate feels like overkill now & the renewal" in m.snippet
    assert "Keyword" not in m.snippet and "Unsubscribe" not in m.snippet
    assert m.url == "https://www.reddit.com/r/CustomerSuccess/comments/1q2w3e/survicate_alternatives_for_a_small_cs_team/"
    assert m.received_at == datetime(2026, 11, 24, 9, 15, 2, tzinfo=timezone.utc)
    assert m.source == "f5bot"


def test_single_comment_with_offset_date():
    [m] = parse_email(load("single_comment.eml"))
    assert m.mention_id == "comment:k7m2p9a"
    assert m.kind == "comment"
    assert m.post_id == "1y3h4j"
    assert m.title == "Auto-send survey when ticket closes"  # "Re: " stripped
    assert m.received_at.utcoffset() == timedelta(hours=5, minutes=30)


def test_multi_match_skips_hacker_news():
    mentions = parse_email(load("multi_match.eml"))
    assert [m.mention_id for m in mentions] == ["post:1t8u9i", "comment:m1n2b3v"]
    second = mentions[1]
    assert second.subreddit == "startups"
    assert second.url.startswith("https://www.reddit.com/r/startups/")  # old.reddit unwrapped + canonical
    assert "Sean Ellis" in second.snippet
    assert all("news.ycombinator" not in m.url for m in mentions)
    # The next section's header doesn't leak into the previous snippet.
    assert "Reddit Comments" not in mentions[0].snippet
    assert "Hacker News" not in mentions[1].snippet


def test_hn_only_email_is_empty_not_error():
    assert parse_email(load("hn_only.eml")) == []


def test_html_only_and_text_only():
    [h] = parse_email(load("html_only.eml"))
    assert h.mention_id == "comment:y5u6i7o" and h.keyword == "simplesat"
    [t] = parse_email(load("text_only.eml"))
    assert t.mention_id == "post:1p0o9i"
    assert t.title == "Moving off Retently, what next?"
    assert t.keyword == "retently"
    assert t.snippet.endswith("only need NPS...")


def test_broken_email_raises():
    with pytest.raises(ParseError):
        parse_email(load("broken.eml"))


def test_not_an_email_raises():
    with pytest.raises(ParseError):
        parse_email(b"\x00\x01garbage")


def test_redirect_unwrapping():
    target = "https://www.reddit.com/r/SaaS/comments/abc123/t/def456/"
    assert unwrap_url("https://f5bot.com/url?u=https%3A%2F%2Fwww.reddit.com%2Fr%2FSaaS%2Fcomments%2Fabc123%2Ft%2Fdef456%2F&i=9&h=x") == target
    assert unwrap_url(f"https://www.google.com/url?q={target}&sa=D") == target
    assert unwrap_url(target) == target
    assert unwrap_url("https://f5bot.com/settings") == "https://f5bot.com/settings"


def test_subject_keyword_fallback():
    msg = EmailMessage()
    msg["From"] = "F5Bot <admin@f5bot.com>"
    msg["Subject"] = "F5Bot found something: nps software"
    msg["Date"] = "Wed, 25 Nov 2026 06:00:00 +0000"
    msg.set_content(
        '<p><a href="https://www.reddit.com/r/SaaS/comments/zz11yy/x/">NPS software for a small team</a></p>'
        "<p>any NPS software that isn't huge?</p>",
        subtype="html",
    )
    [m] = parse_email(bytes(msg))
    assert m.keyword == "nps software"
    assert m.snippet == "any NPS software that isn't huge?"


def test_duplicate_links_collapse():
    msg = EmailMessage()
    msg["Subject"] = "F5Bot found something: survicate"
    msg["Date"] = "Wed, 25 Nov 2026 06:00:00 +0000"
    url = "https://www.reddit.com/r/SaaS/comments/zz11yy/x/"
    msg.set_content(f'<a href="{url}">Survicate pricing</a><a href="{url}">view</a><p>too expensive</p>', subtype="html")
    assert len(parse_email(bytes(msg))) == 1


# ---- real fixtures (drop .eml files in tests/fixtures/f5bot/) ---------------------------


@pytest.mark.skipif(not REAL_FILES, reason="no real F5Bot .eml files in tests/fixtures/f5bot/ yet")
@pytest.mark.parametrize("path", REAL_FILES, ids=[p.name for p in REAL_FILES])
def test_real_fixture_parses(path: Path):
    mentions = parse_email(path.read_bytes())
    for m in mentions:
        assert m.mention_id.startswith(("post:", "comment:"))
        assert m.url.startswith("https://www.reddit.com/r/")
        assert m.subreddit and m.post_id
        assert m.received_at.tzinfo is not None
        assert m.title or m.snippet
    expected_file = REAL / "expected.json"
    if expected_file.exists():
        expected = json.loads(expected_file.read_text(encoding="utf-8")).get(path.name)
        if expected:
            assert len(mentions) == expected["count"]
            for key, value in (expected.get("first") or {}).items():
                assert getattr(mentions[0], key) == value, key
