from __future__ import annotations

from datetime import timedelta

from radar.digest import RunSummary
from radar.pipeline import Context, process
from radar.store import Store

from .fakes import FakeTelegram
from .helpers import NOW, mention


def ctx_for(settings, store, tg, now=NOW, dry_run=False):
    return Context(settings=settings, store=store, telegram=tg, now=now, dry_run=dry_run)


def high(title="What do you use for NPS surveys?", **kw):
    kw.setdefault("sub", "CustomerSuccess")
    return mention(title, "small team", **kw)


def test_high_lead_alerted_once(settings):
    store, tg = Store.memory(), FakeTelegram()
    m = high()
    s = process(ctx_for(settings, store, tg), [m])
    assert s.alerts_sent == 1
    assert tg.kinds().count("alert") == 1
    assert tg.sent[0]["buttons"][0][0][1].startswith("s:replied:")
    # Same mention again (e.g. a second F5Bot email): no new alert.
    s2 = process(ctx_for(settings, store, tg, now=NOW + timedelta(minutes=30)), [m])
    assert s2.alerts_sent == 0 and s2.new_mentions == 0
    assert tg.kinds().count("alert") == 1
    assert store.get_lead(m.mention_id).alerted_at == NOW


def test_second_high_in_same_thread_goes_to_digest(settings):
    store, tg = Store.memory(), FakeTelegram()
    first = high(post_id="thr001")
    process(ctx_for(settings, store, tg), [first])
    second = mention(
        "What do you use for NPS surveys?", "Survicate pricing is too expensive, any alternatives?",
        sub="CustomerSuccess", kind="comment", post_id="thr001",
    )
    later = NOW + timedelta(hours=2)
    s = process(ctx_for(settings, store, tg, now=later), [second])
    assert store.get_lead(second.mention_id).tier == "High"
    assert s.alerts_sent == 0
    assert tg.kinds() == ["alert", "digest", "digest"]
    assert second.url in tg.sent[-1]["text"]
    assert store.get_lead(second.mention_id).alerted_at is None
    assert store.get_lead(second.mention_id).digested_at == later


def test_thread_rule_within_one_batch(settings):
    store, tg = Store.memory(), FakeTelegram()
    a = high(post_id="thr002")
    b = high("Any NPS tool recommendations?", post_id="thr002", kind="comment")
    s = process(ctx_for(settings, store, tg), [a, b])
    assert s.alerts_sent == 1


def test_thread_rule_expires_after_24h(settings):
    store, tg = Store.memory(), FakeTelegram()
    process(ctx_for(settings, store, tg), [high(post_id="thr003")])
    later = NOW + timedelta(hours=25)
    s = process(ctx_for(settings, store, tg, now=later), [high(post_id="thr003", kind="comment", now=later)])
    assert s.alerts_sent == 1


def test_digest_lists_medium_and_is_sent_once(settings):
    store, tg = Store.memory(), FakeTelegram()
    med = mention("Our CSAT response rate is 4%, ideas?", sub="CustomerService", hours=6)
    s = process(ctx_for(settings, store, tg), [med], RunSummary(command="import"))
    assert s.tiers.get("Medium") == 1
    assert tg.kinds() == ["digest"]
    assert med.url in tg.sent[0]["text"]
    assert tg.sent[0]["silent"] is True
    process(ctx_for(settings, store, tg, now=NOW + timedelta(minutes=30)), [])
    assert tg.kinds() == ["digest"]  # nothing new, no second digest


def test_low_only_sends_nothing_but_exports(settings):
    store, tg = Store.memory(), FakeTelegram()
    process(ctx_for(settings, store, tg), [mention("Weekend plans", "nothing", sub="randomsub")])
    assert tg.sent == []
    assert (settings.leads_dir / "latest.json").exists()


def test_failed_send_still_marks_alerted(settings):
    store, tg = Store.memory(), FakeTelegram(fail=True)
    m = high()
    process(ctx_for(settings, store, tg), [m])
    assert store.get_lead(m.mention_id).alerted_at is not None


def test_resend_runs_first(settings):
    store, tg = Store.memory(), FakeTelegram()
    process(ctx_for(settings, store, tg), [])
    assert tg.resends == 1


def test_pipeline_end_to_end_with_real_client_offline(settings):
    """Real TelegramClient, network blocked: everything lands in delivery_failures, data is kept."""
    from radar.telegram import TelegramClient

    store = Store.memory()
    tg = TelegramClient(settings, sleep=lambda s: None)
    m = high()
    process(ctx_for(settings, store, tg), [m])
    files = sorted(p.name for p in settings.delivery_failures_dir.glob("*.txt"))
    assert any(f.endswith("_alert.txt") for f in files)
    assert any(f.endswith("_digest.txt") for f in files)
    assert store.get_lead(m.mention_id) is not None
