"""Logging: console plus a rotating file, with secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from typing import Iterable

from .config import Settings

LOGGER_NAME = "radar"
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_BOT_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
_HANDLER_TAG = "_radar_handler"


class RedactFilter(logging.Filter):
    """Masks known secrets and anything shaped like a Telegram bot token."""

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True)

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return _BOT_TOKEN_RE.sub("bot***", text)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self.redact(record.exc_text)
        return True


def setup_logging(settings: Settings, console: bool = True, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_TAG, False):
            logger.removeHandler(handler)
            handler.close()

    redact = RedactFilter(settings.secrets())
    formatter = logging.Formatter(FORMAT)
    handlers: list[logging.Handler] = []

    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        settings.logs_dir / "radar.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    handlers.append(file_handler)

    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setLevel(level)
        handlers.append(stream)

    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(redact)
        setattr(handler, _HANDLER_TAG, True)
        logger.addHandler(handler)
    return logger
