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


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Every test runs offline: HTTP, raw sockets and subprocess network tools all fail fast."""
    import socket
    import subprocess

    import requests

    import radar.telegram

    def blocked_request(self, method, url, *args, **kwargs):
        raise requests.exceptions.ConnectionError("network disabled in tests")

    def blocked_connect(self, *args, **kwargs):
        raise OSError("network disabled in tests")

    real_run = subprocess.run

    def guarded_run(cmd, *args, **kwargs):
        name = Path(str(cmd[0] if isinstance(cmd, (list, tuple)) else cmd)).name.lower()
        if name.startswith(("curl", "nslookup", "claude")):
            raise OSError("network tools disabled in tests")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(requests.sessions.Session, "request", blocked_request)
    monkeypatch.setattr(socket.socket, "connect", blocked_connect)
    monkeypatch.setattr(subprocess, "run", guarded_run)
    monkeypatch.setattr(radar.telegram, "RETRY_DELAY_SECONDS", 0)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings rooted in a temp dir (data/ lands there), using the repo's YAML config."""
    return make_settings(tmp_path)
