"""Shared fixtures. No test ever makes a network call."""

from __future__ import annotations

from pathlib import Path

import pytest

from radar.config import REPO_ROOT, Settings, load_settings

FIXTURES = Path(__file__).parent / "fixtures"

TEST_ENV = {
    "TELEGRAM_BOT_TOKEN": "123456:TEST-token_abc",
    "TELEGRAM_CHAT_ID": "4242",
    "TIMEZONE": "Asia/Calcutta",
    "IMAP_USER": "radar@example.com",
    "IMAP_APP_PASSWORD": "abcdefghijklmnop",
    "F5BOT_SENDER": "admin@f5bot.com",
}


def make_settings(tmp_path: Path, **overrides: str) -> Settings:
    environ = {**TEST_ENV, **overrides}
    return load_settings(
        env_file=tmp_path / "missing.env",
        config_dir=REPO_ROOT / "config",
        base_dir=tmp_path,
        environ=environ,
        warn=False,
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings rooted in a temp dir (data/ lands there), using the repo's YAML config."""
    return make_settings(tmp_path)
