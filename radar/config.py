"""Settings: loaded from .env plus config/signals.yaml and config/subreddits.yaml."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv import dotenv_values

log = logging.getLogger("radar.config")

REPO_ROOT = Path(__file__).resolve().parent.parent

ENV_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TIMEZONE",
    "IMAP_HOST",
    "IMAP_PORT",
    "IMAP_USER",
    "IMAP_APP_PASSWORD",
    "IMAP_FOLDER",
    "F5BOT_SENDER",
    "IMAP_MARK_SEEN",
    "INGEST_LOOKBACK_DAYS",
    "HIGH_THRESHOLD",
    "MEDIUM_THRESHOLD",
    "MAX_LEAD_AGE_HOURS",
)

SECRET_FIELDS = ("telegram_bot_token", "imap_app_password")
TELEGRAM_FIELDS = ("telegram_bot_token", "telegram_chat_id")
IMAP_FIELDS = ("imap_user", "imap_app_password", "f5bot_sender")
PLACEHOLDER_MARKER = "REPLACE_ME"

# Windows Python has no system tz database and tzdata is not a dependency.
# These zones have no DST, so a fixed offset is exact.
_FIXED_ZONES = {
    "asia/calcutta": timedelta(hours=5, minutes=30),
    "asia/kolkata": timedelta(hours=5, minutes=30),
    "utc": timedelta(0),
    "etc/utc": timedelta(0),
    "gmt": timedelta(0),
}


def is_missing(value: str) -> bool:
    """A value is missing if it is empty or still a REPLACE_ME placeholder."""
    return not value or PLACEHOLDER_MARKER in value


def _to_bool(value: str, default: bool) -> bool:
    if value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _to_int(value: str, default: int, key: str) -> int:
    if value == "":
        return default
    try:
        return int(value)
    except ValueError:
        log.warning("%s=%r is not a number; using %s", key, value, default)
        return default


def mask(value: str) -> str:
    if is_missing(value):
        return "(missing)"
    if len(value) <= 4:
        return "****"
    return value[:2] + "*" * 6 + value[-2:]


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    timezone: str = "Asia/Calcutta"
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_user: str = ""
    imap_app_password: str = ""
    imap_folder: str = "INBOX"
    f5bot_sender: str = ""
    imap_mark_seen: bool = True
    ingest_lookback_days: int = 3
    high_threshold: int = 70
    medium_threshold: int = 45
    max_lead_age_hours: int = 72
    signals: dict[str, Any] = field(default_factory=dict)
    subreddits: dict[str, Any] = field(default_factory=dict)
    base_dir: Path = REPO_ROOT
    env_file: Path | None = None

    # ---- paths -------------------------------------------------------
    @property
    def data_dir(self) -> Path:
        return self.base_dir / "data"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "radar.db"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def leads_dir(self) -> Path:
        return self.data_dir / "leads"

    @property
    def drafts_dir(self) -> Path:
        return self.data_dir / "drafts"

    @property
    def delivery_failures_dir(self) -> Path:
        return self.data_dir / "delivery_failures"

    @property
    def unparsed_dir(self) -> Path:
        return self.data_dir / "unparsed"

    # ---- helpers -----------------------------------------------------
    def zoneinfo(self) -> tzinfo:
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            offset = _FIXED_ZONES.get(self.timezone.lower())
            if offset is not None:
                return timezone(offset, self.timezone)
            log.warning("Unknown TIMEZONE %r; using UTC", self.timezone)
            return timezone.utc

    def secrets(self) -> list[str]:
        return [v for v in (self.telegram_bot_token, self.imap_app_password) if not is_missing(v)]

    def telegram_configured(self) -> bool:
        return not any(is_missing(getattr(self, f)) for f in TELEGRAM_FIELDS)

    def imap_configured(self) -> bool:
        return not any(is_missing(getattr(self, f)) for f in IMAP_FIELDS)

    def missing(self) -> list[str]:
        return [f.upper() for f in TELEGRAM_FIELDS + IMAP_FIELDS if is_missing(getattr(self, f))]

    def masked(self) -> dict[str, str]:
        """Every env setting as a printable string, with secrets masked."""
        out: dict[str, str] = {}
        for key in ENV_KEYS:
            name = key.lower()
            value = getattr(self, name)
            if name in SECRET_FIELDS:
                out[key] = mask(value)
            elif isinstance(value, str) and is_missing(value):
                out[key] = "(missing)"
            else:
                out[key] = str(value)
        return out


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        log.warning("Config file %s not found; using empty config", path)
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def load_settings(
    env_file: Path | None = None,
    config_dir: Path | None = None,
    base_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
    warn: bool = True,
) -> Settings:
    """Load settings. Process environment variables override values in the .env file."""
    base = base_dir or REPO_ROOT
    env_path = env_file if env_file is not None else base / ".env"
    file_values = {k: (v or "") for k, v in dotenv_values(env_path).items()} if env_path.exists() else {}
    env = os.environ if environ is None else environ
    values = {k: str(env.get(k, file_values.get(k, ""))).strip() for k in ENV_KEYS}

    cfg_dir = config_dir or REPO_ROOT / "config"
    settings = Settings(
        telegram_bot_token=values["TELEGRAM_BOT_TOKEN"],
        telegram_chat_id=values["TELEGRAM_CHAT_ID"],
        timezone=values["TIMEZONE"] or "Asia/Calcutta",
        imap_host=values["IMAP_HOST"] or "imap.gmail.com",
        imap_port=_to_int(values["IMAP_PORT"], 993, "IMAP_PORT"),
        imap_user=values["IMAP_USER"],
        imap_app_password=values["IMAP_APP_PASSWORD"].replace(" ", ""),
        imap_folder=values["IMAP_FOLDER"] or "INBOX",
        f5bot_sender=values["F5BOT_SENDER"],
        imap_mark_seen=_to_bool(values["IMAP_MARK_SEEN"], True),
        ingest_lookback_days=_to_int(values["INGEST_LOOKBACK_DAYS"], 3, "INGEST_LOOKBACK_DAYS"),
        high_threshold=_to_int(values["HIGH_THRESHOLD"], 70, "HIGH_THRESHOLD"),
        medium_threshold=_to_int(values["MEDIUM_THRESHOLD"], 45, "MEDIUM_THRESHOLD"),
        max_lead_age_hours=_to_int(values["MAX_LEAD_AGE_HOURS"], 72, "MAX_LEAD_AGE_HOURS"),
        signals=_load_yaml(cfg_dir / "signals.yaml"),
        subreddits=_load_yaml(cfg_dir / "subreddits.yaml"),
        base_dir=base,
        env_file=env_path if env_path.exists() else None,
    )
    if warn:
        if not settings.telegram_configured():
            log.warning("Telegram is not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
        if not settings.imap_configured():
            log.warning("IMAP is not configured (IMAP_USER / IMAP_APP_PASSWORD / F5BOT_SENDER)")
    return settings
