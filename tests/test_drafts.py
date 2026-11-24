from __future__ import annotations

import json
from pathlib import Path

import pytest

from radar.cli import main
from radar.drafts import (
    DraftsFileError,
    allowed_mentions,
    load_drafts,
    newest_unsent,
    ratio_ok,
    send_drafts,
    validate,
)
from radar.models import Draft
from radar.pipeline import Context
from radar.store import Store, iso

from .conftest import FIXTURES
from .fakes import FakeTelegram
from .helpers import NOW

SAMPLE = FIXTURES / "drafts_sample.json"
COMPETITORS = ["survicate", "surveysparrow", "simplesat", "typeform", "delighted"]
GOOD = (
    "A 4% response rate on email CSAT usually comes down to timing and friction. Send it within an hour "
    "of the solve, not the next day, while the conversation is still fresh. Put the one rating question "
    "right in the email body so a single click counts as a response. What helpdesk are you on?"
)


def d(text: str = GOOD, elvan: bool = False) -> Draft:
    return Draft("post:abc123", elvan, text)


# ---- validation rules -------------------------------------------------------------------------


def test_good_draft_has_no_issues():
    assert validate(d(), COMPETITORS) == []


def test_reply_rules_example_passes():
    sample = load_drafts(SAMPLE)
    assert validate(sample.drafts[0], COMPETITORS) == []


@pytest.mark.parametrize("dash", ["—", "–"])
def test_dashes(dash):
    assert "em/en dash" in validate(d(GOOD.replace(", not", f" {dash} not")), COMPETITORS)


def test_emoji():
    assert "emoji" in validate(d(GOOD + " \U0001F642"), COMPETITORS)
    assert "emoji" in validate(d(GOOD + " ✅"), COMPETITORS)


def test_unicode_arrow():
    assert any("arrow" in i for i in validate(d(GOOD.replace("Send it", "Then → send it")), COMPETITORS))


def test_hashtag():
    assert "hashtag" in validate(d(GOOD + " #CustomerSuccess"), COMPETITORS)
    assert "hashtag" not in validate(d(GOOD + " We were the #1 complaint."), COMPETITORS)


def test_too_long_and_too_short():
    assert any("too long" in i for i in validate(d("word " * 181), COMPETITORS))
    assert any("too short" in i for i in validate(d("Send it sooner."), COMPETITORS))
    assert not any("too" in i for i in validate(d("word " * 180), COMPETITORS))


def test_elvan_without_disclosure():
    text = GOOD + " Elvan does this with a Zendesk app."
    assert any("without" in i for i in validate(d(text, elvan=True), COMPETITORS))
    ok = GOOD + " Full disclosure, I’m on the Elvan team and we do this."
    assert validate(d(ok, elvan=True), COMPETITORS) == []


def test_elvan_named_but_flag_false():
    assert "names Elvan but mentions_elvan is false" in validate(d(GOOD + " Elvan does this."), COMPETITORS)


def test_competitor_price():
    assert "competitor price" in validate(d(GOOD + " Survicate costs $99 a month now."), COMPETITORS)
    # A price in a different sentence from the competitor name is fine.
    ok = GOOD + " Simplesat is solid. I'm on the Elvan team: we charge $49 a month."
    assert "competitor price" not in validate(d(ok, elvan=True), COMPETITORS)


def test_links():
    assert any("link" in i for i in validate(d(GOOD + " See https://example.com/x"), COMPETITORS))
    assert any("link" in i for i in validate(d(GOOD + " Try survicate.com"), COMPETITORS))
    with_plain = GOOD + " Full disclosure, I'm on the Elvan team, it's at elvan.ai."
    assert validate(d(with_plain, elvan=True), COMPETITORS) == []
    assert any("link" in i for i in validate(d(GOOD + " I'm on the Elvan team: https://elvan.ai", elvan=True), COMPETITORS))


# ---- ratio math -------------------------------------------------------------------------------


def test_ratio_math():
    assert allowed_mentions(mentions=1, replies=6, batch=1) == 0
    assert allowed_mentions(mentions=0, replies=10, batch=3) == 2
    assert allowed_mentions(mentions=0, replies=0, batch=5) == 1
    assert allowed_mentions(mentions=0, replies=0, batch=4) == 0
    assert allowed_mentions(mentions=5, replies=0, batch=3) == 0
    assert allowed_mentions(0, 100, 2) == 2  # never more than the batch


def _posted(store: Store, n: int, elvan: int) -> None:
    for i in range(n):
        store.save_draft(f"post:x{i:04d}", i < elvan, "t", "", NOW, NOW, None)
    store.conn.execute("UPDATE drafts SET posted = 1")
    store.conn.commit()


def test_ratio_ok_uses_posted_replies():
    store = Store.memory()
    _posted(store, 6, elvan=1)
    assert not ratio_ok(store, 1, 1, NOW)
    store2 = Store.memory()
    _posted(store2, 10, elvan=0)
    assert ratio_ok(store2, 2, 3, NOW)
    assert not ratio_ok(store2, 3, 3, NOW)


# ---- files ------------------------------------------------------------------------------------


def test_malformed_file_clear_error(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(DraftsFileError, match="not valid JSON"):
        load_drafts(bad)
    bad.write_text('{"drafts": [{"mention_id": "abc", "mentions_elvan": false, "draft": "x"}]}', encoding="utf-8")
    with pytest.raises(DraftsFileError, match="mention_id"):
        load_drafts(bad)
    bad.write_text('{"drafts": [{"mention_id": "post:abc", "mentions_elvan": "no", "draft": "x"}]}', encoding="utf-8")
    with pytest.raises(DraftsFileError, match="mentions_elvan"):
        load_drafts(bad)
    bad.write_text('{"leads": []}', encoding="utf-8")
    with pytest.raises(DraftsFileError, match='"drafts" list'):
        load_drafts(bad)


def _ctx(settings, store, tg):
    return Context(settings=settings, store=store, telegram=tg, now=NOW)


def _install(settings, name: str = "2026-11-25_1130.json", text: str | None = None) -> Path:
    settings.drafts_dir.mkdir(parents=True, exist_ok=True)
    path = settings.drafts_dir / name
    path.write_text(text or SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def test_send_drafts_sends_saves_and_skips_empty(settings):
    store, tg = Store.memory(), FakeTelegram()
    _install(settings)
    # 0 posted replies so far: a batch of 2 allows 0 Elvan mentions.
    result = send_drafts(_ctx(settings, store, tg))
    assert result.sent == 2 and result.skipped_empty == 1
    assert tg.kinds() == ["draft", "draft"]
    first, second = tg.sent[0]["text"], tg.sent[1]["text"]
    assert first.startswith("<b>NEEDS EDIT: over mention ratio, remove Elvan</b>")
    assert "<pre>" in first and "Mentions Elvan: yes" in first
    assert "NEEDS EDIT" not in second
    assert tg.sent[1]["buttons"][0][0][1].startswith("s:replied:")
    rows = store.conn.execute("SELECT mention_id, mentions_elvan, message_id, sent_at FROM drafts").fetchall()
    assert [r[0] for r in rows] == ["post:1q2w3e", "post:1x2c3v"]
    assert all(r[2] is not None and r[3] == iso(NOW) for r in rows)


def test_mention_allowed_when_ratio_has_room(settings):
    store, tg = Store.memory(), FakeTelegram()
    _posted(store, 10, elvan=0)
    _install(settings)
    send_drafts(_ctx(settings, store, tg))
    assert "NEEDS EDIT" not in tg.sent[0]["text"]


def test_bad_draft_flagged(settings):
    store, tg = Store.memory(), FakeTelegram()
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    data["drafts"][1]["draft"] = data["drafts"][1]["draft"].replace(", not the next day,", " — not the next day —")
    _install(settings, text=json.dumps(data))
    send_drafts(_ctx(settings, store, tg))
    assert tg.sent[1]["text"].startswith("<b>NEEDS EDIT: em/en dash</b>")


def test_already_sent_file_skipped(settings):
    store, tg = Store.memory(), FakeTelegram()
    path = _install(settings)
    send_drafts(_ctx(settings, store, tg))
    sent = len(tg.sent)
    assert newest_unsent(settings.drafts_dir, store) is None
    again = send_drafts(_ctx(settings, store, tg), path)
    assert again.already_sent
    assert len(tg.sent) == sent


def test_newest_unsent_picks_latest(settings):
    store = Store.memory()
    _install(settings, "2026-11-24_2100.json")
    _install(settings, "2026-11-25_0900.json")
    assert newest_unsent(settings.drafts_dir, store).name == "2026-11-25_0900.json"


def test_cli_send_drafts_dry_run(settings, capsys):
    _install(settings)
    assert main(["send-drafts", "--dry-run"], settings=settings) == 0
    out = capsys.readouterr().out
    assert "[dry-run] telegram draft" in out
    assert "sent 2 draft(s)" in out
    assert not settings.db_path.exists()


def test_cli_send_drafts_malformed(settings, capsys):
    _install(settings, text="{broken")
    assert main(["send-drafts"], settings=settings) == 2
    assert "not valid JSON" in capsys.readouterr().out
