from __future__ import annotations

import io
import logging

from radar.logger import RedactFilter, setup_logging


def _capture(secrets):
    logger = logging.getLogger("radar_test_capture")
    logger.handlers.clear()
    logger.propagate = False
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.addFilter(RedactFilter(secrets))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    return logger, buf


def test_token_redacted():
    token = "123456789:AAFakeTokenValue_xyz-123"
    logger, buf = _capture([token])
    logger.info("token is %s", token)
    assert token not in buf.getvalue()
    assert "***" in buf.getvalue()


def test_token_redacted_inside_telegram_url_even_if_unknown():
    logger, buf = _capture([])
    logger.warning("POST https://api.telegram.org/bot987654:ZZsecretZZ/sendMessage failed")
    out = buf.getvalue()
    assert "ZZsecretZZ" not in out
    assert "bot***" in out


def test_password_redacted():
    logger, buf = _capture(["abcdefghijklmnop"])
    logger.error("login failed for pw=abcdefghijklmnop")
    assert "abcdefghijklmnop" not in buf.getvalue()


def test_exception_text_redacted():
    logger, buf = _capture(["s3cretpass"])
    try:
        raise RuntimeError("auth failed: s3cretpass")
    except RuntimeError:
        logger.exception("boom")
    assert "s3cretpass" not in buf.getvalue()
    assert "RuntimeError" in buf.getvalue()


def test_setup_logging_writes_file_with_redaction(settings):
    logger = setup_logging(settings, console=False)
    try:
        logger.info("sending with %s and %s", settings.telegram_bot_token, settings.imap_app_password)
        for h in logger.handlers:
            h.flush()
        text = (settings.logs_dir / "radar.log").read_text(encoding="utf-8")
        assert "sending with" in text
        assert settings.telegram_bot_token not in text
        assert settings.imap_app_password not in text
    finally:
        for h in list(logger.handlers):
            logger.removeHandler(h)
            h.close()
