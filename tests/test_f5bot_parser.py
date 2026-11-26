from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import pytest

from radar.sources.f5bot_parser import ParseError, parse_email, parse_email_result, unwrap_url

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


# ---- real fixtures: exact expectations ------------------------------------------------------

CSAT = "F5Bot found something_ csat.eml"
CSAT_1 = "F5Bot found something_ csat (1).eml"
CSAT_2 = "F5Bot found something_ csat (2).eml"

# (kind, mention_id, subreddit, post_id, author), in email order
REAL_EXPECTED = {
    CSAT: [  # 4 = 1 post + 3 comments
        ("post", "post:1y52bmg", "UPSC", "1y52bmg", "Spare-upsc-80"),
        ("comment", "comment:19t5tnte5nfsw", "UPSC", "1y52bmg", "Far_Coffee2067"),
        ("comment", "comment:19t5trodfip29", "UPSC", "1y50jns", "Cardamom_Cutie"),
        ("comment", "comment:19t5vvjgewitf", "Bolehland", "1y53b2a", "km_jhyn"),
    ],
    CSAT_1: [  # 4 = 1 post + 3 comments
        ("comment", "comment:19t5yek5a4fj8", "GPSC", "1xlxcnz", "Plenty_Entrance6554"),
        ("comment", "comment:19t5z6l7yyr51", "Romania", "1y53zpx", "disc0mbobulated"),
        ("comment", "comment:19t6371nwhlpe", "Separation_Anxiety", "1y4t67g", "No_Explorer8369"),
        ("post", "post:1y56d7z", "Romania2", "1y56d7z", "IUsedToBeAMod"),
    ],
    CSAT_2: [  # 3 = 2 posts + 1 comment
        ("post", "post:1y56u9r", "UPSCBharat", "1y56u9r", "True_Fisherman_417"),
        ("post", "post:1y56x9s", "UPSCCRITICS", "1y56x9s", "True_Fisherman_417"),
        ("comment", "comment:19t658vsfw6sj", "FitTheWord", "1wej8sv", "PrestigeFlight2022"),
    ],
}
NO_EXCERPT = {"post:1y56d7z"}
BOILERPLATE = ("Keyword was found", "F5Bot", "phishing", "PhishProtection", "RedPulse", "advertise", "Unsubscribe")


def _real(name: str) -> bytes:
    path = REAL / name
    if not path.exists():
        pytest.skip(f"real fixture {name} not present")
    return path.read_bytes()


@pytest.mark.parametrize("name", list(REAL_EXPECTED))
def test_real_fixture_hits(name):
    result = parse_email_result(_real(name))
    assert result.failed_hits == []
    got = [(m.kind, m.mention_id, m.subreddit, m.post_id, m.author) for m in result.mentions]
    assert got == REAL_EXPECTED[name]
    for m in result.mentions:
        assert m.keyword == "csat"
        assert m.title
        # F5Bot sends no excerpt when the keyword was only in a link post's title.
        assert m.snippet or m.mention_id in NO_EXCERPT
        assert not any(b.lower() in m.snippet.lower() for b in BOILERPLATE), m.snippet
        assert "?" not in m.url and m.url.startswith(f"https://www.reddit.com/r/{m.subreddit}/comments/{m.post_id}/")
        if m.kind == "comment":
            assert m.url.endswith(f"/_/{m.mention_id.split(':')[1]}/")


def test_real_fixture_totals():
    mentions = [m for name in REAL_EXPECTED for m in parse_email(_real(name))]
    assert len(mentions) == 11
    assert sum(m.kind == "post" for m in mentions) == 4
    assert sum(m.kind == "comment" for m in mentions) == 7


def test_real_fixture_fields():
    first, second = parse_email(_real(CSAT))[:2]
    assert first.title == "Help with CSAT resources please"
    assert first.snippet.startswith("● Can somebody please tell me where to find CSAT pyqs book")
    assert first.received_at == datetime(2026, 11, 25, 10, 37, 13, tzinfo=timezone.utc)
    assert second.title == "Help with CSAT resources please"  # comments carry the thread title
    assert second.snippet.startswith("If you are good at maths")
    # The last hit before the sponsor block must not swallow the ad copy.
    last = parse_email(_real(CSAT_1))[-1]
    assert last.mention_id == "post:1y56d7z" and last.snippet == ""
    assert "phishing" not in parse_email(_real(CSAT_2))[-1].snippet.lower()


# ---- skipped hits are never silent ----------------------------------------------------------


def _f5bot_email(groups: list[tuple[str, list[tuple[str, str, str]]]]) -> bytes:
    """An email in the real F5Bot layout. groups: [(keyword, [(section, reddit_url, title)])]."""
    from urllib.parse import quote

    body = ['<p class="f5-heading">F5Bot found something!</p>']
    for keyword, hits in groups:
        body.append(f'<h2 class="f5-hit-heading">Keyword: &quot;{keyword}&quot;</h2>')
        for i, (section, url, title) in enumerate(hits):
            href = f"https://f5bot.com/url?u={quote(url, safe='')}&amp;i={i}&amp;h=abc"
            body.append(
                f'<p class="f5-hit"><span class="f5-meta">{section}</span> <a href="{href}">{title}</a> '
                f'<span class="f5-meta">by someone_{i}</span><br><span class="f5-excerpt">'
                f"<strong>Keyword was found in submission title.</strong><br>excerpt about {keyword} {i}</span></p>"
            )
    body.append('<p>Do you have comments or suggestions about F5Bot? Please hit reply!</p>')
    body.append('<table><tr><td class="f5-ad"><p>Buy our thing. Try <a href="https://f5bot.com/url?a=1&i=23&h=x">Thing.com</a></p></td></tr></table>')
    body.append('<p><a href="https://f5bot.com/dash">Manage your alerts</a></p>')
    msg = EmailMessage()
    msg["From"] = "F5Bot Alerts <alerts@f5bot.com>"
    msg["Subject"] = f"F5Bot found something: {groups[0][0]}"
    msg["Date"] = "Wed, 25 Nov 2026 10:00:00 +0000"
    msg.set_content("<html><body>" + "".join(body) + "</body></html>", subtype="html")
    return bytes(msg)


GOOD = "https://www.reddit.com/r/SaaS/comments/1y52bmg/c/19t5tnte5nfsw?context=3"
BAD = "https://www.reddit.com/r/SaaS/comments/not*an*id/c/19t5tnte5nfsw?context=3"


def test_unparseable_hit_is_logged_and_returned(caplog):
    raw = _f5bot_email([("nps tool", [("Reddit Comments (/r/SaaS/):", GOOD, "Good thread"), ("Reddit Comments (/r/SaaS/):", BAD, "Bad thread")])])
    with caplog.at_level("WARNING", logger="radar.f5bot"):
        result = parse_email_result(raw)
    assert [m.mention_id for m in result.mentions] == ["comment:19t5tnte5nfsw"]
    assert result.failed_hits == [BAD]
    assert any(BAD in r.getMessage() for r in caplog.records)


def test_email_with_only_unparseable_hits_raises_with_urls():
    raw = _f5bot_email([("nps tool", [("Reddit Posts (/r/SaaS/):", BAD, "Bad thread")])])
    with pytest.raises(ParseError) as info:
        parse_email_result(raw)
    assert info.value.failed_hits == [BAD]
    assert BAD in str(info.value)


def test_keyword_headings_apply_to_the_hits_below_them():
    raw = _f5bot_email([
        ("survicate", [("Reddit Posts (/r/SaaS/):", "https://www.reddit.com/r/SaaS/comments/aaa111/x/", "A")]),
        ("nps tool", [("Reddit Comments (/r/SaaS/):", "https://www.reddit.com/r/SaaS/comments/bbb222/c/ccc333?context=3", "B")]),
    ])
    a, b = parse_email(raw)
    assert (a.keyword, b.keyword) == ("survicate", "nps tool")
    assert a.author == "someone_0" and b.author == "someone_0"
    assert a.snippet == "excerpt about survicate 0"
    assert b.snippet == "excerpt about nps tool 0"  # no ad copy, no heading
