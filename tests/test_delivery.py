"""Delivery safeguards: nothing that fails to reach Telegram is lost or sent twice."""

from __future__ import annotations

import json
import os
import time
from datetime import timedelta

from radar.drafts import send_drafts
from radar.feedback import run_feedback
from radar.pipeline import Context, resend_pending
from radar.store import Store
from radar.telegram import TelegramClient

from .conftest import FIXTURES
from .fakes import FakeHTTP, FakeResponse, conn_error, fail, ok
from .helpers import NOW

DRAFTS = FIXTURES / "drafts_sample.json"


def client(settings, script=None):
    sleeps: list[float] = []
    http = FakeHTTP(list(script or []))
    return TelegramClient(settings, http=http, sleep=sleeps.append), http, sleeps


def ctx(settings, store, tg, now=NOW):
    return Context(settings=settings, store=store, telegram=tg, now=now)


def install_drafts(settings, name="2026-11-27_1930.json", text=None):
    settings.drafts_dir.mkdir(parents=True, exist_ok=True)
    path = settings.drafts_dir / name
    path.write_text(text or DRAFTS.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def queued(settings):
    return sorted(settings.delivery_failures_dir.glob("*.txt"))


def drafts_rows(store):
    return store.conn.execute("SELECT mention_id, message_id, sent_at FROM drafts ORDER BY id").fetchall()


def sent_texts(http):
    return [c[1]["text"] for c in http.calls if c[0] == "sendMessage"]


# ---- the 19:30 scenario ------------------------------------------------------------------


def test_failed_draft_is_sent_by_the_next_send_drafts(settings):
    store = Store.memory()
    install_drafts(settings)
    tg, _, _ = client(settings, [conn_error()] * 2)  # internet down at 19:30
    r = send_drafts(ctx(settings, store, tg))
    assert r.sent == 0 and r.queued == 2
    assert len(queued(settings)) == 2
    assert all(row[1] is None and row[2] is None for row in drafts_rows(store))  # not delivered yet
    meta = TelegramClient.load_failure(queued(settings)[0])[3]
    assert meta["kind"] == "draft" and isinstance(meta["draft_id"], int)

    # Next send-drafts run, with no new drafts file: the queued drafts go out first.
    tg2, http2, _ = client(settings)
    r2 = send_drafts(ctx(settings, store, tg2, now=NOW + timedelta(hours=4)))
    assert r2.resent == 2
    assert queued(settings) == []
    rows = drafts_rows(store)
    assert all(row[1] is not None for row in rows)  # message ids recorded, so Replied taps count
    assert all(row[2] is not None for row in rows)
    assert "<pre>" in sent_texts(http2)[0]


def test_failed_draft_is_sent_by_the_next_feedback_run(settings):
    store = Store.memory()
    install_drafts(settings)
    send_drafts(ctx(settings, store, client(settings, [conn_error()] * 2)[0]))
    assert len(queued(settings)) == 2
    tg, _, _ = client(settings, [ok(501), ok(502), FakeResponse(200, {"ok": True, "result": []})])
    run_feedback(ctx(settings, store, tg, now=NOW + timedelta(hours=4)))  # the 23:30 run
    assert queued(settings) == []
    assert [r[1] for r in drafts_rows(store)] == [501, 502]


def test_new_drafts_wait_behind_old_ones(settings):
    """Oldest first: earlier undelivered drafts arrive before the new batch."""
    store = Store.memory()
    install_drafts(settings)
    send_drafts(ctx(settings, store, client(settings, [conn_error()] * 2)[0]))
    data = json.loads(DRAFTS.read_text(encoding="utf-8"))
    data["drafts"] = [dict(data["drafts"][1], draft=data["drafts"][1]["draft"] + " One more line for today.")]
    install_drafts(settings, "2026-11-28_1930.json", json.dumps(data))
    tg, http, _ = client(settings)
    r = send_drafts(ctx(settings, store, tg, now=NOW + timedelta(days=1)))
    assert r.resent == 2 and r.sent == 1
    assert "One more line for today." in sent_texts(http)[-1]


# ---- no internet for a whole run ----------------------------------------------------------


def test_offline_run_does_not_retry_every_message(settings):
    tg, http, sleeps = client(settings, [conn_error()] * 2)
    assert tg.send("first", kind="alert") is None
    assert tg.send("second", kind="alert") is None
    assert tg.send("third", kind="digest") is None
    assert len(http.calls) == 2  # only the first message tried the network (plus its one retry)
    assert len(sleeps) == 1
    assert len(queued(settings)) == 3


def test_telegram_server_error_counts_as_offline(settings):
    tg, http, _ = client(settings, [fail(502, "Bad Gateway"), fail(502, "Bad Gateway")])
    tg.send("x")
    tg.send("y")
    assert len(http.calls) == 2
    assert len(queued(settings)) == 2


# ---- rate limits ----------------------------------------------------------------------------


def test_rate_limit_waits_and_delivers(settings):
    too_many = FakeResponse(
        429, {"ok": False, "description": "Too Many Requests: retry after 3", "parameters": {"retry_after": 3}}
    )
    tg, _, sleeps = client(settings, [too_many, ok(9)])
    assert tg.send("hello") == 9
    assert sleeps == [3]
    assert queued(settings) == []


def test_backlog_resend_is_paced(settings):
    tg, _, _ = client(settings, [conn_error()] * 2)
    for i in range(3):
        tg.send(f"m{i}")
    tg2, http, sleeps = client(settings)
    assert tg2.resend_failures() == (3, 0)
    assert len(sleeps) == 2  # a short pause between messages
    assert sent_texts(http) == ["m0", "m1", "m2"]


# ---- one bad message must not block the queue ------------------------------------------------


def test_refused_message_moves_to_dead_and_the_rest_still_send(settings):
    tg, _, _ = client(settings, [conn_error()] * 2)
    tg.send("bad one")
    tg.send("good one")
    tg2, http, _ = client(settings, [fail(400, "Bad Request: message is too long")])
    assert tg2.resend_failures() == (1, 0)
    assert len(tg2.dead_failures()) == 1
    assert "bad one" in tg2.dead_failures()[0].read_text(encoding="utf-8")
    assert sent_texts(http)[-1] == "good one"


def test_unreadable_file_moves_to_dead(settings):
    settings.delivery_failures_dir.mkdir(parents=True)
    (settings.delivery_failures_dir / "20261127_000000_000000_alert.txt").write_bytes(b"\xff\xfe\xfa broken")
    (settings.delivery_failures_dir / "20261127_000001_000000_alert.txt").write_text("fine", encoding="utf-8")
    tg, _, _ = client(settings)
    assert tg.resend_failures() == (1, 0)
    assert len(tg.dead_failures()) == 1


# ---- two runs at once never double-send ---------------------------------------------------------


def test_claimed_file_is_skipped_by_a_second_run(settings):
    tg, _, _ = client(settings)
    path = tg.preserve("hello", None, "alert")
    claimed = path.with_suffix(".sending")
    os.replace(path, claimed)  # another run is sending it right now
    tg2, http, _ = client(settings)
    assert tg2.resend_failures() == (0, 0)
    assert http.calls == []
    assert claimed.exists()


def test_stale_claim_from_a_crashed_run_is_recovered(settings):
    tg, _, _ = client(settings)
    path = tg.preserve("hello", None, "alert")
    claimed = path.with_suffix(".sending")
    os.replace(path, claimed)
    old = time.time() - 3600
    os.utime(claimed, (old, old))
    tg2, _, _ = client(settings)
    assert tg2.resend_failures() == (1, 0)
    assert not claimed.exists()


def test_preserve_leaves_no_partial_files(settings):
    tg, _, _ = client(settings)
    tg.preserve("x", [[("Skip", "s:skipped:a")]], "alert", {"draft_id": 3})
    names = [p.name for p in settings.delivery_failures_dir.iterdir()]
    assert len(names) == 1 and names[0].endswith("_alert.txt")
    text, buttons, kind, meta = TelegramClient.load_failure(settings.delivery_failures_dir / names[0])
    assert (text, kind, meta["draft_id"]) == ("x", "alert", 3)
    assert buttons == [[("Skip", "s:skipped:a")]]


# ---- re-running send-drafts never duplicates ------------------------------------------------------


def test_same_drafts_file_copied_under_new_name_is_not_resent(settings):
    store = Store.memory()
    install_drafts(settings)
    tg, http, _ = client(settings)
    send_drafts(ctx(settings, store, tg))
    sent_before = len(http.calls)
    install_drafts(settings, "2026-11-27_1931.json")  # e.g. Claude wrote the same drafts again
    r = send_drafts(ctx(settings, store, tg))
    assert r.duplicates == 2 and r.sent == 0
    assert len(http.calls) == sent_before
    assert len(drafts_rows(store)) == 2


def test_unsent_files_all_go_out_and_stale_ones_are_skipped(settings):
    store = Store.memory()
    old = install_drafts(settings, "2026-11-20_1930.json")
    stamp = (NOW - timedelta(days=6)).timestamp()
    os.utime(old, (stamp, stamp))
    data = json.loads(DRAFTS.read_text(encoding="utf-8"))
    data["drafts"] = [data["drafts"][1]]
    install_drafts(settings, "2026-11-26_1930.json", json.dumps(data))
    install_drafts(settings, "2026-11-27_1930.json")
    tg, _, _ = client(settings)
    r = send_drafts(ctx(settings, store, tg))
    assert [p.name for p in r.stale] == ["2026-11-20_1930.json"]
    assert [p.name for p in r.files] == ["2026-11-26_1930.json", "2026-11-27_1930.json"]
    assert r.sent == 2 and r.duplicates == 1  # the 26th's draft is identical to one in the 27th's file


# ---- reporting ------------------------------------------------------------------------------------


def test_doctor_reports_waiting_messages(settings):
    from radar.doctor import check_deliveries

    tg, _, _ = client(settings, [conn_error()] * 2)
    assert check_deliveries(tg).ok
    tg.send("x")
    c = check_deliveries(tg)
    assert not c.ok and "1 waiting" in c.detail


def test_resend_pending_records_draft_delivery(settings):
    store = Store.memory()
    draft_id = store.save_draft("post:abc123", False, "text", "", NOW, None, None)
    tg, _, _ = client(settings, [conn_error()] * 2)
    tg.send("<pre>text</pre>", kind="draft", meta={"draft_id": draft_id})
    tg2, _, _ = client(settings, [ok(777)])
    assert resend_pending(ctx(settings, store, tg2)) == (1, 0)
    assert store.conn.execute("SELECT message_id FROM drafts WHERE id=?", (draft_id,)).fetchone()[0] == 777
