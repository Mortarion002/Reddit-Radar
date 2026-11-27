from __future__ import annotations

from radar.doctor import check_claude, check_env, format_checks, run_doctor
from radar.sources.f5bot_mail import Mailbox
from radar.telegram import TelegramClient

from .fakes import FakeHTTP, FakeResponse, conn_error
from .fakes_imap import FakeIMAP
from .helpers import NOW


def _mailbox(fake: FakeIMAP):
    def make(settings, store, dry_run=False):
        return Mailbox(settings, store, dry_run=dry_run, imap_factory=fake.factory)

    return make


def _env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# test\n", encoding="utf-8")
    from radar.config import REPO_ROOT, load_settings
    from .conftest import TEST_ENV

    return load_settings(env_file=env, config_dir=REPO_ROOT / "config", base_dir=tmp_path, environ=TEST_ENV, warn=False)


def _by_name(checks):
    return {c.name: c for c in checks}


def test_all_pass(tmp_path):
    settings = _env_file(tmp_path)
    tg = TelegramClient(settings, http=FakeHTTP([FakeResponse(200, {"ok": True, "result": {"username": "ElvanRadarBot"}})]))
    fake = FakeIMAP({"1": b"x", "2": b"y"})
    checks = run_doctor(settings, NOW, telegram=tg, mailbox_factory=_mailbox(fake), which=lambda n: "/bin/claude")
    assert all(c.ok for c in checks), format_checks(checks)
    names = _by_name(checks)
    assert "@ElvanRadarBot" in names["Telegram reachable"].detail
    assert "2 from admin@f5bot.com" in names["F5Bot emails in last 7 days"].detail
    assert set(names) == {
        ".env present", "Telegram reachable", "Undelivered messages", "IMAP login", "F5Bot emails in last 7 days", "DB writable", "claude on PATH",
    }
    assert fake.readonly is True  # doctor never changes the mailbox


def test_failures_reported(settings):
    tg = TelegramClient(settings, http=FakeHTTP([conn_error()]))
    fake = FakeIMAP(fail_login=True)
    checks = _by_name(run_doctor(settings, NOW, telegram=tg, mailbox_factory=_mailbox(fake), which=lambda n: None))
    assert not checks[".env present"].ok  # no .env file in the temp dir
    assert not checks["Telegram reachable"].ok
    assert not checks["IMAP login"].ok and "login failed" in checks["IMAP login"].detail
    assert not checks["F5Bot emails in last 7 days"].ok
    assert checks["DB writable"].ok
    assert not checks["claude on PATH"].ok


def test_no_recent_f5bot_mail_fails(tmp_path):
    settings = _env_file(tmp_path)
    checks = _by_name(
        run_doctor(settings, NOW, telegram=TelegramClient(settings, http=FakeHTTP()), mailbox_factory=_mailbox(FakeIMAP({})))
    )
    assert checks["IMAP login"].ok
    assert not checks["F5Bot emails in last 7 days"].ok


def test_placeholders_fail_env_check(tmp_path):
    from radar.config import load_settings

    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=REPLACE_ME\n", encoding="utf-8")
    s = load_settings(env_file=tmp_path / ".env", base_dir=tmp_path, environ={}, warn=False)
    c = check_env(s)
    assert not c.ok and "TELEGRAM_BOT_TOKEN" in c.detail


def test_dry_run_db_untouched(settings):
    checks = _by_name(
        run_doctor(settings, NOW, dry_run=True, telegram=TelegramClient(settings, http=FakeHTTP()),
                   mailbox_factory=_mailbox(FakeIMAP({"1": b"x"})), which=lambda n: None)
    )
    assert checks["DB writable"].detail == "skipped (dry-run)"
    assert not settings.db_path.exists()


def test_check_claude():
    assert check_claude(lambda n: "C:/npm/claude.cmd").ok
    assert not check_claude(lambda n: None).ok


def test_format():
    from radar.doctor import Check

    text = format_checks([Check("a", True, "fine"), Check("longer", False, "bad")])
    assert text.splitlines() == ["[PASS] a       fine", "[FAIL] longer  bad"]
