from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from radar.ingest import run_scan, warn_unreachable
from radar.pipeline import Context
from radar.sources.f5bot_mail import Mailbox, MailboxError, imap_date, search_since
from radar.store import Store

from .conftest import FIXTURES
from .fakes import FakeTelegram
from .fakes_imap import FakeIMAP
from .helpers import NOW

SYNTH = FIXTURES / "f5bot_synthetic"


def raw(name: str) -> bytes:
    return (SYNTH / name).read_bytes()


def mailbox_factory(fake: FakeIMAP):
    def make(settings, store, dry_run=False):
        return Mailbox(settings, store, dry_run=dry_run, imap_factory=fake.factory)

    return make


def ctx(settings, store, tg=None, now=NOW, dry_run=False):
    return Context(settings=settings, store=store, telegram=tg or FakeTelegram(), now=now, dry_run=dry_run)


def test_imap_date_is_locale_independent():
    assert imap_date(date(2026, 11, 5)) == "05-Nov-2026"


def test_search_since_first_and_later_runs(settings):
    store = Store.memory()
    assert search_since(store, settings, NOW) == (NOW - timedelta(days=3)).date()
    store.record_email("9", "<m>", datetime(2026, 11, 20, 23, 0, tzinfo=timezone.utc), 1, "ok")
    assert search_since(store, settings, NOW) == date(2026, 11, 19)


def test_only_new_uids_fetched_with_peek(settings):
    store = Store.memory()
    store.record_email("1", "<old>", NOW, 1, "ok")
    fake = FakeIMAP({"1": raw("single_post.eml"), "2": raw("single_comment.eml")})
    with Mailbox(settings, store, imap_factory=fake.factory) as mb:
        emails = mb.fetch_new(now=NOW)
    assert [uid for uid, _ in emails] == ["2"]
    fetches = fake.of("FETCH")
    assert fetches == [("FETCH", "2", "(BODY.PEEK[])")]
    search = fake.of("SEARCH")[0]
    assert '"admin@f5bot.com"' in search and "FROM" in search and "SINCE" in search
    assert fake.logged_out


def test_seen_set_only_after_success(settings):
    store = Store.memory()
    fake = FakeIMAP({"5": raw("single_post.eml"), "6": raw("broken.eml"), "7": raw("hn_only.eml")})
    run_scan(ctx(settings, store), mailbox_factory=mailbox_factory(fake))
    stored = [c[1] for c in fake.of("STORE")]
    assert stored == ["5", "7"]  # the broken email stays unread for a human to look at
    assert all(c[2:] == ("+FLAGS", "(\\Seen)") for c in fake.of("STORE"))
    assert not any(c[0] in ("EXPUNGE", "MOVE", "COPY") for c in fake.commands)


def test_mark_seen_disabled(tmp_path):
    from .conftest import make_settings

    s = make_settings(tmp_path, IMAP_MARK_SEEN="false")
    fake = FakeIMAP({"5": raw("single_post.eml")})
    run_scan(ctx(s, Store.memory()), mailbox_factory=mailbox_factory(fake))
    assert fake.of("STORE") == []
    assert fake.readonly is True


def test_auth_error_warns_and_exits_cleanly(settings):
    store, tg = Store.memory(), FakeTelegram()
    fake = FakeIMAP(fail_login=True)
    summary = run_scan(ctx(settings, store, tg), mailbox_factory=mailbox_factory(fake))
    assert any("login failed" in e for e in summary.errors)
    assert tg.kinds() == ["warning"]
    assert tg.sent[0]["text"].startswith("F5Bot inbox unreachable: login failed")
    assert settings.imap_app_password not in tg.sent[0]["text"]
    assert tg.resends == 1


def test_connect_error_is_a_mailbox_error(settings):
    fake = FakeIMAP(fail_connect=True)
    with pytest.raises(MailboxError):
        Mailbox(settings, Store.memory(), imap_factory=fake.factory).connect()


def test_warning_throttled_6h(settings):
    store, tg = Store.memory(), FakeTelegram()
    fake = FakeIMAP(fail_login=True)
    run_scan(ctx(settings, store, tg), mailbox_factory=mailbox_factory(fake))
    run_scan(ctx(settings, store, tg, now=NOW + timedelta(hours=1)), mailbox_factory=mailbox_factory(fake))
    assert tg.kinds() == ["warning"]
    run_scan(ctx(settings, store, tg, now=NOW + timedelta(hours=7)), mailbox_factory=mailbox_factory(fake))
    assert tg.kinds() == ["warning", "warning"]


def test_warning_resets_after_recovery(settings):
    store, tg = Store.memory(), FakeTelegram()
    run_scan(ctx(settings, store, tg), mailbox_factory=mailbox_factory(FakeIMAP(fail_login=True)))
    run_scan(ctx(settings, store, tg, now=NOW + timedelta(hours=1)), mailbox_factory=mailbox_factory(FakeIMAP()))
    run_scan(ctx(settings, store, tg, now=NOW + timedelta(hours=2)), mailbox_factory=mailbox_factory(FakeIMAP(fail_login=True)))
    assert tg.kinds().count("warning") == 2


def test_not_configured_is_a_clean_failure(tmp_path):
    from .conftest import make_settings

    s = make_settings(tmp_path, IMAP_APP_PASSWORD="REPLACE_ME")
    tg = FakeTelegram()
    summary = run_scan(ctx(s, Store.memory(), tg), mailbox_factory=mailbox_factory(FakeIMAP()))
    assert any("IMAP not configured" in e for e in summary.errors)


def test_dry_run_changes_nothing(settings, tmp_path):
    path = tmp_path / "radar.db"
    fake = FakeIMAP({"5": raw("single_post.eml"), "6": raw("broken.eml")})
    with Store.open(path, dry_run=True) as store:
        tg = FakeTelegram()
        run_scan(ctx(settings, store, tg, dry_run=True), mailbox_factory=mailbox_factory(fake))
    assert fake.of("STORE") == []
    assert fake.readonly is True
    assert not path.exists()
    assert not settings.unparsed_dir.exists()


def test_dry_run_warning_not_sent(settings):
    store, tg = Store.memory(), FakeTelegram()
    assert not warn_unreachable(ctx(settings, store, tg, dry_run=True), "x")
    assert tg.sent == []
