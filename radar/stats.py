"""Weekly stats: volume, lead quality by tier/signal/subreddit/keyword, and what to tune."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .digest import esc
from .store import Store, iso

TAPPED = ("replied", "skipped", "not_relevant")


def precision(replied: int, skipped: int, not_relevant: int) -> float | None:
    """(replied + skipped) / (replied + skipped + not_relevant), or None with no taps."""
    total = replied + skipped + not_relevant
    return (replied + skipped) / total if total else None


@dataclass
class KeywordQuality:
    keyword: str
    mentions: int
    sent: int  # High or Medium
    not_relevant: int
    tapped: int

    @property
    def not_relevant_rate(self) -> float | None:
        return self.not_relevant / self.tapped if self.tapped else None

    @property
    def noise_rate(self) -> float:
        """Share of this keyword's mentions that were Low or dropped (never sent)."""
        return 1 - self.sent / self.mentions if self.mentions else 0.0


@dataclass
class Stats:
    days: float
    runs: int = 0
    emails_read: int = 0
    mentions: int = 0
    parse_errors: int = 0
    by_tier: Counter = field(default_factory=Counter)
    by_signal: Counter = field(default_factory=Counter)
    by_subreddit: Counter = field(default_factory=Counter)
    by_keyword: Counter = field(default_factory=Counter)
    status_counts: Counter = field(default_factory=Counter)
    precision_high: float | None = None
    precision_medium: float | None = None
    keywords: list[KeywordQuality] = field(default_factory=list)
    top_not_relevant_terms: list[tuple[str, int]] = field(default_factory=list)
    replies_posted: int = 0
    elvan_mentions: int = 0


def compute_stats(store: Store, days: float, now: datetime) -> Stats:
    since = iso(now - timedelta(days=days))
    s = Stats(days=days)
    row = store.conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(emails_read), 0) FROM runs WHERE started_at >= ?", (since,)
    ).fetchone()
    s.runs, s.emails_read = int(row[0]), int(row[1])
    s.parse_errors = store.conn.execute(
        "SELECT COUNT(*) FROM processed_emails WHERE status = 'parse_error' AND received_at >= ?", (since,)
    ).fetchone()[0]

    rows = store.conn.execute(
        "SELECT m.subreddit, m.keyword, l.tier, l.primary_signal, l.status, l.matched_terms"
        " FROM mentions m JOIN leads l ON l.mention_id = m.mention_id WHERE m.received_at >= ?",
        (since,),
    ).fetchall()
    s.mentions = len(rows)
    tier_status: dict[str, Counter] = {"High": Counter(), "Medium": Counter()}
    kw: dict[str, Counter] = {}
    nr_terms: Counter = Counter()
    for sub, keyword, tier, signal, status, terms in rows:
        keyword = (keyword or "(none)").lower()
        s.by_tier[tier] += 1
        s.by_keyword[keyword] += 1
        counts = kw.setdefault(keyword, Counter())
        counts["mentions"] += 1
        if tier in ("High", "Medium"):
            s.by_signal[signal] += 1
            s.by_subreddit[f"r/{sub}"] += 1
            s.status_counts[status] += 1
            tier_status[tier][status] += 1
            counts["sent"] += 1
            if status in TAPPED:
                counts["tapped"] += 1
            if status == "not_relevant":
                counts["not_relevant"] += 1
                nr_terms.update(json.loads(terms or "[]"))

    s.precision_high = precision(*(tier_status["High"][k] for k in TAPPED))
    s.precision_medium = precision(*(tier_status["Medium"][k] for k in TAPPED))
    s.keywords = sorted(
        (KeywordQuality(k, c["mentions"], c["sent"], c["not_relevant"], c["tapped"]) for k, c in kw.items()),
        key=lambda q: (-q.mentions, q.keyword),
    )
    s.top_not_relevant_terms = nr_terms.most_common(5)
    s.replies_posted, s.elvan_mentions = store.reply_stats(now - timedelta(days=days))
    return s


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _counter_line(counter: Counter, limit: int = 8) -> str:
    return ", ".join(f"{esc(str(k))} {v}" for k, v in counter.most_common(limit)) or "none"


def format_stats(s: Stats) -> str:
    days = f"{s.days:g}"
    lines = [
        f"<b>Reddit Radar stats, last {days} days</b>",
        f"Runs {s.runs} | emails {s.emails_read} | mentions {s.mentions} | parse errors {s.parse_errors}",
        "",
        "<b>Leads</b>",
        "By tier: " + ", ".join(f"{t} {s.by_tier.get(t, 0)}" for t in ("High", "Medium", "Low", "Dropped")),
        "By signal: " + _counter_line(s.by_signal),
        "By subreddit: " + _counter_line(s.by_subreddit),
        "",
        "<b>Your taps (High + Medium)</b>",
        "Status: " + ", ".join(f"{k} {s.status_counts.get(k, 0)}" for k in ("new", "replied", "skipped", "not_relevant")),
        f"Precision: High {_pct(s.precision_high)} | Medium {_pct(s.precision_medium)} (target: High 60%+)",
        "",
        "<b>F5Bot keywords</b> (mentions | sent | not relevant | noise)",
    ]
    for q in s.keywords[:20] or []:
        nr = f"{q.not_relevant}/{q.tapped} ({_pct(q.not_relevant_rate)})" if q.tapped else "-"
        lines.append(f"{esc(q.keyword)}: {q.mentions} | {q.sent} | {nr} | {_pct(q.noise_rate)}")
    if not s.keywords:
        lines.append("no mentions yet")
    lines.append("")
    if s.top_not_relevant_terms:
        lines.append("Top terms behind not-relevant leads: " + ", ".join(f"{esc(t)} {n}" for t, n in s.top_not_relevant_terms))
    lines.append(f"Replies posted: {s.replies_posted} | naming Elvan: {s.elvan_mentions}")
    return "\n".join(lines)
