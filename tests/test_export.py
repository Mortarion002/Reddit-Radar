from __future__ import annotations

import json

from radar.export import EXPORT_CAP, export_leads
from radar.scorer import ScoringConfig, build_lead
from radar.store import Store, iso

from .helpers import NOW, mention


def _seed(store, settings, mentions):
    cfg = ScoringConfig.from_settings(settings)
    store.upsert_mentions(mentions, now=NOW)
    for m in mentions:
        store.save_lead(build_lead(m, cfg, NOW))


def test_shape(settings):
    store = Store.memory()
    m = mention("What do you use for NPS surveys?", "small team", sub="CustomerSuccess", kind="comment")
    _seed(store, settings, [m, mention("Weekend plans", "nothing", sub="randomsub", hours=30)])
    path = export_leads(store, settings, NOW)
    data = json.loads(path.read_text(encoding="utf-8"))
    latest = json.loads((settings.leads_dir / "latest.json").read_text(encoding="utf-8"))
    assert data == latest
    assert path.name == "2026-11-25_1130.json"  # 06:00 UTC -> 11:30 IST
    assert data["generated_at"] == "2026-11-25T11:30:00+05:30"
    assert data["elvan_mentions_last_7_days"] == 0
    assert data["replies_last_7_days"] == 0
    assert len(data["leads"]) == 1
    lead = data["leads"][0]
    assert set(lead) == {
        "mention_id", "kind", "post_id", "subreddit", "thread_title", "snippet", "url", "age_hours",
        "keyword", "primary_signal", "signals", "competitor", "score", "tier",
    }
    assert lead["mention_id"] == m.mention_id
    assert lead["kind"] == "comment"
    assert lead["age_hours"] == 1.0
    assert store.get_lead(m.mention_id).exported_at == NOW


def test_cap(settings):
    store = Store.memory()
    _seed(store, settings, [mention(f"What do you use for NPS surveys {i}?", sub="CustomerSuccess") for i in range(14)])
    data = json.loads(export_leads(store, settings, NOW).read_text(encoding="utf-8"))
    assert len(data["leads"]) == EXPORT_CAP


def test_drafted_and_actioned_leads_excluded(settings):
    store = Store.memory()
    a = mention("What do you use for NPS surveys?", sub="CustomerSuccess")
    b = mention("Survicate pricing is too expensive?", sub="CustomerSuccess")
    c = mention("Any NPS tool recommendations?", sub="CustomerSuccess")
    _seed(store, settings, [a, b, c])
    store.conn.execute(
        "INSERT INTO drafts(mention_id, created_at, mentions_elvan, draft_text) VALUES(?, ?, 0, 'x')",
        (a.mention_id, iso(NOW)),
    )
    store.conn.commit()
    store.set_status(b.mention_id, "skipped", NOW)
    data = json.loads(export_leads(store, settings, NOW).read_text(encoding="utf-8"))
    assert [l["mention_id"] for l in data["leads"]] == [c.mention_id]


def test_no_leads_refreshes_latest_only(settings):
    store = Store.memory()
    path = export_leads(store, settings, NOW)
    assert path.name == "latest.json"
    assert json.loads(path.read_text(encoding="utf-8"))["leads"] == []
    assert [p.name for p in settings.leads_dir.iterdir()] == ["latest.json"]


def test_dry_run_writes_nothing(settings):
    store = Store.memory()
    _seed(store, settings, [mention("What do you use for NPS surveys?", sub="CustomerSuccess")])
    assert export_leads(store, settings, NOW, dry_run=True) is None
    assert not settings.leads_dir.exists()
