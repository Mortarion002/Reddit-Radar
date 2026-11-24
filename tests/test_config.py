from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from radar.config import ENV_KEYS, REPO_ROOT, load_settings, mask


def _write_env(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_loads_env_example_style_values(tmp_path):
    env = _write_env(
        tmp_path / ".env",
        "TELEGRAM_BOT_TOKEN=111:abc\nTELEGRAM_CHAT_ID=99\nIMAP_PORT=1993\n"
        "IMAP_MARK_SEEN=false\nINGEST_LOOKBACK_DAYS=5\nHIGH_THRESHOLD=75\n",
    )
    s = load_settings(env_file=env, base_dir=tmp_path, environ={}, warn=False)
    assert s.telegram_bot_token == "111:abc"
    assert s.telegram_chat_id == "99"
    assert s.imap_port == 1993
    assert s.imap_mark_seen is False
    assert s.ingest_lookback_days == 5
    assert s.high_threshold == 75
    assert s.medium_threshold == 45
    assert s.imap_host == "imap.gmail.com"
    assert s.db_path == tmp_path / "data" / "radar.db"


def test_env_example_has_every_key():
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ENV_KEYS:
        assert f"{key}=" in text


def test_environment_overrides_file(tmp_path):
    env = _write_env(tmp_path / ".env", "TELEGRAM_CHAT_ID=1\n")
    s = load_settings(env_file=env, base_dir=tmp_path, environ={"TELEGRAM_CHAT_ID": "2"}, warn=False)
    assert s.telegram_chat_id == "2"


def test_yaml_loads(tmp_path):
    s = load_settings(env_file=tmp_path / "nope.env", environ={}, warn=False)
    assert "survicate" in s.signals["competitors"]["priority"]
    assert "nps" in s.signals["product_terms"]
    assert s.subreddits["bonus"]["customersuccess"] == 8
    assert isinstance(s.subreddits["blocklist"], list)


def test_missing_optional_values_warn_not_crash(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="radar.config"):
        s = load_settings(env_file=tmp_path / "none.env", base_dir=tmp_path, environ={}, warn=True)
    assert not s.telegram_configured()
    assert not s.imap_configured()
    assert "TELEGRAM_BOT_TOKEN" in s.missing()
    assert any("Telegram is not configured" in r.message for r in caplog.records)
    assert any("IMAP is not configured" in r.message for r in caplog.records)


def test_placeholders_count_as_missing(tmp_path):
    env = _write_env(tmp_path / ".env", "TELEGRAM_BOT_TOKEN=REPLACE_ME\nTELEGRAM_CHAT_ID=REPLACE_ME\n")
    s = load_settings(env_file=env, base_dir=tmp_path, environ={}, warn=False)
    assert not s.telegram_configured()
    assert s.masked()["TELEGRAM_BOT_TOKEN"] == "(missing)"


def test_masked_never_shows_secrets(settings):
    shown = " ".join(settings.masked().values())
    assert settings.telegram_bot_token not in shown
    assert settings.imap_app_password not in shown
    assert mask("abcdefghijklmnop") == "ab******op"


def test_bad_number_falls_back(tmp_path):
    env = _write_env(tmp_path / ".env", "IMAP_PORT=abc\n")
    s = load_settings(env_file=env, base_dir=tmp_path, environ={}, warn=False)
    assert s.imap_port == 993


def test_app_password_spaces_removed(tmp_path):
    s = load_settings(
        env_file=tmp_path / "x.env", base_dir=tmp_path, environ={"IMAP_APP_PASSWORD": "abcd efgh ijkl mnop"}, warn=False
    )
    assert s.imap_app_password == "abcdefghijklmnop"


def test_zoneinfo_india_offset(settings):
    tz = settings.zoneinfo()
    assert datetime(2026, 11, 25, 12, 0, tzinfo=tz).utcoffset() == timedelta(hours=5, minutes=30)
