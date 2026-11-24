"""Guard: the app never talks to reddit.com. Reddit URLs are parsed and shown, never fetched."""

from __future__ import annotations

import re
from pathlib import Path

RADAR = Path(__file__).resolve().parent.parent / "radar"

# A call to an HTTP client whose argument list mentions reddit.com.
NETWORK_CALL = re.compile(
    r"\b(requests|session|urllib\.request|urlopen|httpx|http\.client|curl)\b[\w.]*\s*\([^)]*reddit\.com",
    re.IGNORECASE | re.DOTALL,
)
FORBIDDEN_IMPORT = re.compile(
    r"^\s*(import|from)\s+(praw|asyncpraw|selenium|playwright|feedparser)\b", re.MULTILINE
)


def _sources():
    files = sorted(RADAR.rglob("*.py"))
    assert files, "no radar sources found"
    return [(p, p.read_text(encoding="utf-8")) for p in files]


def test_no_requests_to_reddit():
    for path, text in _sources():
        assert not NETWORK_CALL.search(text), f"{path} appears to call reddit.com over the network"


def test_no_praw_or_browser_automation():
    for path, text in _sources():
        assert not FORBIDDEN_IMPORT.search(text), f"{path} imports a forbidden Reddit/browser library"


def test_guard_catches_bad_code():
    assert NETWORK_CALL.search('requests.get("https://www.reddit.com/r/SaaS/new.json")')
    assert NETWORK_CALL.search('session.post(\n    "https://oauth.reddit.com/api/v1/access_token")')
    assert FORBIDDEN_IMPORT.search("import praw\n")
    assert FORBIDDEN_IMPORT.search("from selenium import webdriver\n")
