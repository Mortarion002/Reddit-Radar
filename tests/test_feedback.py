from __future__ import annotations

from datetime import timedelta

from radar.cli import main
from radar.feedback import run_feedback
from radar.pipeline import Context
from radar.scorer import ScoringConfig, build_lead
from radar.store import Store

from .fakes import FakeTelegram
from .helpers import NOW, mention

CHAT = 4242  # matches TELEGRAM_CHAT_ID in tests/conftest.py


def seed(settings, store):
    m = mention("What do you use for NPS surveys?", sub="CustomerSuccess")
    store.upsert_mentions([m], now=NOW)
    lead = build_lead(m, ScoringConfig.from_settings(settings), NOW)
    store.save_lead(lead)
    return lead


def tap(update_id, data, message_id=500, chat=CHAT, text="HIGH 90 | tool_request"):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb{update_id}",
            "data": data,
            "message": {"message_id": message_id, "chat": {"id": chat}, "text": text},
        },
    }


def say(update_id, text, chat=CHAT):
    return {"update_id": update_id, "message": {"message_id": 900 + update_id, "chat": {"id": chat}, "text": text}}


def ctx(settings, store, tg, now=NOW):
    return Context(settings=settings, store=store, telegram=tg, now=now)


def test_callback_updates_status_answers_and_edits(settings):
    store = Store.memory()
    lead = seed(settings, store)
    tg = FakeTelegram(updates=[tap(10, f"s:skipped:{lead.short_id}")])
    result = run_feedback(ctx(settings, store, tg))
    got = store.get_lead(lead.mention_id)
    assert got.status == "skipped" and got.status_at == NOW
    assert result.statuses == [(lead.mention_id, "skipped")]
    assert tg.answers == ["Marked skipped"]
    chat_id, message_id, buttons = tg.edits[0]
    assert (chat_id, message_id) == (str(CHAT), 500)
    assert [label for label, _ in buttons[0]] == ["Replied", "* Skip", "Not relevant"]


def test_offset_saved_and_used(settings):
    store = Store.memory()
    lead = seed(settings, store)
    tg = FakeTelegram(updates=[tap(10, f"s:replied:{lead.short_id}"), say(11, "hello")])
    run_feedback(ctx(settings, store, tg))
    assert store.get_state("last_update_id") == "11"
    run_feedback(ctx(settings, store, tg))
    assert tg.offsets == [None, 12]


def test_foreign_chat_ignored(settings):
    store = Store.memory()
    lead = seed(settings, store)
    tg = FakeTelegram(updates=[tap(10, f"s:not_relevant:{lead.short_id}", chat=999), say(11, "/pause", chat=999)])
    result = run_feedback(ctx(settings, store, tg))
    assert store.get_lead(lead.mention_id).status == "new"
    assert store.get_state("paused") is None
    assert result.ignored == 2
    assert tg.answers == [] and tg.sent == []
    assert store.get_state("last_update_id") == "11"


def test_malformed_callback_does_not_crash(settings):
    store = Store.memory()
    lead = seed(settings, store)
    tg = FakeTelegram(
        updates=[
            tap(10, "garbage"),
            tap(11, "s:replied:ffffffffff"),  # unknown short id
            {"update_id": 12, "callback_query": None},
            {"update_id": 13},
            tap(14, f"s:replied:{lead.short_id}"),
        ]
    )
    result = run_feedback(ctx(settings, store, tg))
    assert tg.answers[:2] == ["Unknown button", "Lead not found"]
    assert store.get_lead(lead.mention_id).status == "replied"
    assert store.get_state("last_update_id") == "14"
    assert result.updates == 5


def test_replayed_update_is_noop(settings):
    store = Store.memory()
    lead = seed(settings, store)
    run_feedback(ctx(settings, store, FakeTelegram(updates=[tap(10, f"s:replied:{lead.short_id}")])))
    store.set_status(lead.mention_id, "skipped", NOW)  # changed some other way
    tg = FakeTelegram(updates=[tap(10, f"s:replied:{lead.short_id}")])
    tg.get_updates = lambda offset: [tap(10, f"s:replied:{lead.short_id}")]  # server replays it
    result = run_feedback(ctx(settings, store, tg))
    assert result.updates == 0
    assert store.get_lead(lead.mention_id).status == "skipped"


def test_replied_on_draft_marks_posted(settings):
    store = Store.memory()
    lead = seed(settings, store)
    store.save_draft(lead.mention_id, True, "draft text", "", NOW, NOW, 777)
    tg = FakeTelegram(updates=[tap(10, f"s:replied:{lead.short_id}", message_id=777)])
    run_feedback(ctx(settings, store, tg))
    assert store.conn.execute("SELECT posted FROM drafts").fetchone()[0] == 1
    assert store.reply_stats(NOW - timedelta(days=7)) == (1, 1)
    # Changing your mind on the same message un-posts it.
    tg2 = FakeTelegram(updates=[tap(11, f"s:skipped:{lead.short_id}", message_id=777)])
    run_feedback(ctx(settings, store, tg2))
    assert store.conn.execute("SELECT posted FROM drafts").fetchone()[0] == 0
    assert store.reply_stats(NOW - timedelta(days=7)) == (0, 0)


def test_replied_on_alert_does_not_mark_draft_posted(settings):
    store = Store.memory()
    lead = seed(settings, store)
    store.save_draft(lead.mention_id, True, "draft text", "", NOW, NOW, 777)
    tg = FakeTelegram(updates=[tap(10, f"s:replied:{lead.short_id}", message_id=500)])
    run_feedback(ctx(settings, store, tg))
    assert store.conn.execute("SELECT posted FROM drafts").fetchone()[0] == 0
    assert store.reply_stats(NOW - timedelta(days=7)) == (1, 0)


def test_resent_draft_without_message_id_falls_back(settings):
    store = Store.memory()
    lead = seed(settings, store)
    store.save_draft(lead.mention_id, True, "draft text", "", NOW, NOW, None)
    tg = FakeTelegram(updates=[tap(10, f"s:replied:{lead.short_id}", message_id=123, text="<pre>x</pre>\nMentions Elvan: yes")])
    run_feedback(ctx(settings, store, tg))
    assert store.conn.execute("SELECT posted FROM drafts").fetchone()[0] == 1


def test_pause_resume_and_stats_commands(settings):
    store = Store.memory()
    tg = FakeTelegram(updates=[say(10, "/pause"), say(11, "/stats@ElvanRadarBot"), say(12, "just chatting")])
    result = run_feedback(ctx(settings, store, tg))
    assert store.get_state("paused") == "1"
    assert result.commands == ["/pause", "/stats"]
    assert tg.kinds() == ["reply", "stats"]
    assert "Reddit Radar stats" in tg.sent[1]["text"]
    run_feedback(ctx(settings, store, FakeTelegram(updates=[say(13, "/resume")])))
    assert store.get_state("paused") == "0"


def test_pause_makes_scan_skip(settings, capsys):
    with Store.open(settings.db_path) as store:
        run_feedback(ctx(settings, store, FakeTelegram(updates=[say(10, "/pause")])))
    assert main(["scan"], settings=settings) == 0
    assert "paused" in capsys.readouterr().out
    with Store.open(settings.db_path) as store:
        run_feedback(ctx(settings, store, FakeTelegram(updates=[say(11, "/resume")])))
    main(["scan"], settings=settings)
    assert "paused" not in capsys.readouterr().out


def test_cli_feedback_offline(settings, capsys):
    # Network is blocked in tests: getUpdates fails, the command still exits 0.
    assert main(["feedback"], settings=settings) == 0
    assert "0 update(s)" in capsys.readouterr().out
