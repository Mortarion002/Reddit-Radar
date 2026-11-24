"""End to end: mocked mailbox with fixture emails -> store -> leads, Telegram mocked."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from radar.cli import main
from radar.ingest import run_ingest_eml, run_scan
from radar.pipeline import Context
from radar.store import Store

from .conftest import FIXTURES
from .fakes import FakeTelegram
from .fakes_imap import FakeIMAP
from .test_f5bot_mail import mailbox_factory, raw

SYNTH = FIXTURES / "f5bot_synthetic"
# Shortly after the newest synthetic email, so nothing is older than 72h.
NOW = datetime(2026, 11, 25, 7, 0, tzinfo=timezone.utc)


def ctx(settings, store, tg, now=NOW):
    return Context(settings=settings, store=store, telegram=tg, now=now)


def fixture_mailbox() -> FakeIMAP:
    names = ["single_post.eml", "single_comment.eml", "multi_match.eml", "hn_only.eml", "broken.eml"]
    return FakeIMAP({str(10 + i): raw(n) for i, n in enumerate(names)})


def test_scan_end_to_end(settings):
    store, tg = Store.memory(), FakeTelegram()
    fake = fixture_mailbox()
    summary = run_scan(ctx(settings, store, tg), mailbox_factory=mailbox_factory(fake))

    assert summary.emails_read == 5
    assert summary.mentions_found == 4
    assert summary.new_mentions == 4
    assert summary.parse_errors == 1
    leads = {l.mention_id: l for l in store.get_leads()}
    assert set(leads) == {"post:1q2w3e", "comment:k7m2p9a", "post:1t8u9i", "comment:m1n2b3v"}
    assert leads["post:1q2w3e"].primary_signal == "alternative_search"
    row = store.conn.execute("SELECT email_uid FROM mentions WHERE mention_id='post:1q2w3e'").fetchone()
    assert row[0] == "10"
    statuses = dict(store.conn.execute("SELECT uid, status FROM processed_emails").fetchall())
    assert statuses == {"10": "ok", "11": "ok", "12": "ok", "13": "no_reddit", "14": "parse_error"}
    assert (settings.unparsed_dir / "14.eml").exists()
    assert "alert" in tg.kinds()


def test_second_scan_reprocesses_nothing(settings):
    store, tg = Store.memory(), FakeTelegram()
    fake = fixture_mailbox()
    run_scan(ctx(settings, store, tg), mailbox_factory=mailbox_factory(fake))
    sent_before = len(tg.sent)
    summary = run_scan(ctx(settings, store, tg, NOW + timedelta(minutes=30)), mailbox_factory=mailbox_factory(fake))
    assert summary.emails_read == 0 and summary.new_mentions == 0
    assert len(fake.of("FETCH")) == 5  # only the first scan fetched
    assert len(tg.sent) == sent_before


def test_ingest_eml_folder(settings, capsys):
    store, tg = Store.memory(), FakeTelegram()
    summary = run_ingest_eml(ctx(settings, store, tg), SYNTH)
    assert summary.emails_read == 7
    assert summary.parse_errors == 1
    assert summary.new_mentions == 6
    assert "broken.eml: parse_error" in capsys.readouterr().out
    assert (settings.unparsed_dir / "file_broken.eml").exists()


def test_ingest_eml_cli_dry_run(settings, capsys):
    assert main(["ingest-eml", "--dir", str(SYNTH), "--dry-run"], settings=settings) == 0
    out = capsys.readouterr().out
    assert "single_post.eml: ok, 1 mention(s)" in out
    assert not settings.db_path.exists()
    assert not settings.unparsed_dir.exists()


def test_scan_skips_when_paused(settings, capsys):
    with Store.open(settings.db_path) as store:
        store.set_state("paused", "1")
    assert main(["scan"], settings=settings) == 0
    assert "paused" in capsys.readouterr().out
    with Store.open(settings.db_path) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_scan_cli_with_unreachable_inbox_exits_zero(settings, capsys):
    # Network is blocked in tests, so the real IMAP connect fails: a warning, not a crash.
    assert main(["scan"], settings=settings) == 0
    out = capsys.readouterr().out
    assert "error: imap:" in out
    files = list(settings.delivery_failures_dir.glob("*_warning.txt"))
    assert len(files) == 1
