from __future__ import annotations

from radar.cli import main
from radar.scorer import ScoringConfig, build_lead
from radar.stats import compute_stats, format_stats, precision
from radar.store import Store

from .helpers import NOW, mention


def test_precision_math():
    assert precision(2, 1, 1) == 0.75
    assert precision(0, 0, 4) == 0.0
    assert precision(0, 0, 0) is None
    assert precision(3, 0, 0) == 1.0


def _add(store, cfg, title, snippet="", keyword="nps tool", status=None, **kw):
    m = mention(title, snippet, keyword=keyword, **kw)
    store.upsert_mentions([m], now=NOW)
    lead = build_lead(m, cfg, NOW)
    store.save_lead(lead)
    if status:
        store.set_status(m.mention_id, status, NOW)
    return lead


def _seeded(settings):
    store = Store.memory()
    cfg = ScoringConfig.from_settings(settings)
    high = dict(sub="CustomerSuccess")
    _add(store, cfg, "What do you use for NPS surveys?", status="replied", **high)
    _add(store, cfg, "Any NPS tool recommendations?", status="skipped", **high)
    _add(store, cfg, "Looking for an NPS tool?", status="not_relevant", **high)
    _add(store, cfg, "Survicate pricing is too expensive?", keyword="survicate", status="not_relevant", **high)
    _add(store, cfg, "Our CSAT response rate is 4%, ideas?", keyword="csat survey", status="replied", sub="CustomerService", hours=6)
    _add(store, cfg, "Weekend plans", "nothing", keyword="survicate", sub="randomsub", kind="comment", hours=30)
    _add(store, cfg, "I built an NPS tool, roast my site", keyword="nps tool")
    store.record_email("1", "<a>", NOW, 0, "parse_error")
    run = store.start_run("scan", NOW)
    store.finish_run(run, emails_read=4, now=NOW)
    return store


def test_stats_counts_and_precision(settings):
    s = compute_stats(_seeded(settings), 7, NOW)
    assert s.mentions == 7
    assert s.runs == 1 and s.emails_read == 4 and s.parse_errors == 1
    assert s.by_tier["High"] == 4 and s.by_tier["Medium"] == 1
    assert s.by_tier["Low"] == 1 and s.by_tier["Dropped"] == 1
    assert s.status_counts["not_relevant"] == 2
    assert s.precision_high == 0.5  # replied 1 + skipped 1 of 4 tapped
    assert s.precision_medium == 1.0
    assert s.replies_posted == 2


def test_per_keyword_breakdown(settings):
    s = compute_stats(_seeded(settings), 7, NOW)
    by_kw = {q.keyword: q for q in s.keywords}
    nps = by_kw["nps tool"]
    assert (nps.mentions, nps.sent, nps.not_relevant, nps.tapped) == (4, 3, 1, 3)
    assert abs(nps.not_relevant_rate - 1 / 3) < 1e-9
    assert nps.noise_rate == 0.25  # the dropped promo post
    surv = by_kw["survicate"]
    assert (surv.mentions, surv.sent, surv.not_relevant) == (2, 1, 1)
    assert surv.not_relevant_rate == 1.0
    assert by_kw["csat survey"].not_relevant_rate == 0.0


def test_top_not_relevant_terms(settings):
    s = compute_stats(_seeded(settings), 7, NOW)
    terms = dict(s.top_not_relevant_terms)
    assert terms["looking for"] == 1
    assert terms["survicate"] == 1
    assert len(s.top_not_relevant_terms) <= 5


def test_format_mentions_keywords(settings):
    text = format_stats(compute_stats(_seeded(settings), 7, NOW))
    assert "Precision: High 50% | Medium 100%" in text
    assert "survicate: 2 | 1 | 1/1 (100%)" in text
    assert "Replies posted: 2" in text


def test_empty_db(settings):
    text = format_stats(compute_stats(Store.memory(), 7, NOW))
    assert "no mentions yet" in text
    assert "Precision: High n/a" in text


def test_cli_stats_no_send(settings, capsys):
    with Store.open(settings.db_path) as store:
        cfg = ScoringConfig.from_settings(settings)
        _add(store, cfg, "What do you use for NPS surveys?", sub="CustomerSuccess")
    assert main(["stats", "--days", "7", "--no-send"], settings=settings) == 0
    out = capsys.readouterr().out
    assert "Reddit Radar stats, last 7 days" in out
    assert "<b>" not in out
    assert not settings.delivery_failures_dir.exists()
