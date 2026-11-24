from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

from radar.scorer import ScoringConfig, build_lead
from radar.store import MIGRATIONS, Store

from .helpers import NOW, mention


@pytest.fixture
def store():
    s = Store.memory()
    yield s
    s.close()


def test_tables_created(store):
    names = {r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"mentions", "leads", "drafts", "processed_emails", "runs", "state"} <= names
    assert store.schema_version == len(MIGRATIONS)


def test_upsert_returns_only_new(store):
    a, b = mention("a"), mention("b")
    assert [m.mention_id for m in store.upsert_mentions([a, b], now=NOW)] == [a.mention_id, b.mention_id]
    c = mention("c")
    assert [m.mention_id for m in store.upsert_mentions([a, c, b], now=NOW)] == [c.mention_id]
    assert store.get_mention(a.mention_id).title == "a"


def test_duplicate_within_batch(store):
    a = mention("a")
    assert len(store.upsert_mentions([a, a], now=NOW)) == 1


def test_email_uid_saved(store):
    a = mention("a")
    store.upsert_mentions([a], email_uids={a.mention_id: "77"}, now=NOW)
    row = store.conn.execute("SELECT email_uid FROM mentions").fetchone()
    assert row[0] == "77"


def test_lead_roundtrip_and_status_kept_on_rescore(store, settings):
    cfg = ScoringConfig.from_settings(settings)
    m = mention("What do you use for NPS surveys?", sub="CustomerSuccess")
    store.upsert_mentions([m], now=NOW)
    lead = build_lead(m, cfg, NOW)
    store.save_lead(lead)
    got = store.get_lead(m.mention_id)
    assert got.tier == lead.tier and got.score == lead.score
    assert got.signals == lead.signals
    assert got.mention.received_at == m.received_at
    assert store.set_status(m.mention_id, "replied", NOW)
    store.mark("alerted_at", [m.mention_id], NOW)
    store.save_lead(build_lead(m, cfg, NOW))  # rescore
    got = store.get_lead(m.mention_id)
    assert got.status == "replied"
    assert got.alerted_at == NOW


def test_set_status_rejects_unknown(store):
    with pytest.raises(ValueError):
        store.set_status("post:x", "maybe")


def test_get_leads_filters(store, settings):
    cfg = ScoringConfig.from_settings(settings)
    fresh = mention("What do you use for NPS surveys?", sub="CustomerSuccess", hours=1)
    low = mention("Macros thread", "we use simplesat", kind="comment", sub="randomsub", hours=1)
    old = mention("Looking for an NPS tool?", hours=50)
    store.upsert_mentions([fresh, low, old], now=NOW)
    for m in (fresh, low, old):
        store.save_lead(build_lead(m, cfg, NOW))
    assert {l.mention_id for l in store.get_leads(tiers=["High"])} >= {fresh.mention_id}
    assert low.mention_id not in {l.mention_id for l in store.get_leads(tiers=["High", "Medium"])}
    recent = store.get_leads(since=NOW - timedelta(hours=24))
    assert old.mention_id not in {l.mention_id for l in recent}
    scores = [l.score for l in store.get_leads()]
    assert scores == sorted(scores, reverse=True)


def test_run_log(store):
    run_id = store.start_run("import", NOW)
    store.finish_run(run_id, emails_read=2, mentions_found=5, new_mentions=3, leads_high=1, errors=["x"], now=NOW)
    row = store.get_run(run_id)
    assert row["command"] == "import"
    assert row["mentions_found"] == 5 and row["new_mentions"] == 3 and row["leads_high"] == 1
    assert row["finished_at"] is not None
    assert row["errors"] == '["x"]'


def test_state(store):
    assert store.get_state("missing") is None
    assert store.get_state("missing", "d") == "d"
    store.set_state("paused", "1")
    store.set_state("paused", "0")
    assert store.get_state("paused") == "0"


def test_reopen_keeps_data_and_version(tmp_path):
    path = tmp_path / "radar.db"
    with Store.open(path) as s:
        s.upsert_mentions([mention("x")], now=NOW)
    with Store.open(path) as s:
        assert s.conn.execute("SELECT COUNT(*) FROM mentions").fetchone()[0] == 1
        assert s.schema_version == len(MIGRATIONS)


def test_dry_run_writes_nothing(tmp_path):
    path = tmp_path / "radar.db"
    with Store.open(path) as s:
        s.upsert_mentions([mention("x")], now=NOW)
    with Store.open(path, dry_run=True) as s:
        s.upsert_mentions([mention("y")], now=NOW)
        s.set_state("k", "v")
        assert s.conn.execute("SELECT COUNT(*) FROM mentions").fetchone()[0] == 2
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM mentions").fetchone()[0] == 1
    conn.close()


def test_dry_run_without_db_creates_no_file(tmp_path):
    path = tmp_path / "data" / "radar.db"
    with Store.open(path, dry_run=True) as s:
        s.upsert_mentions([mention("y")], now=NOW)
    assert not path.exists()
