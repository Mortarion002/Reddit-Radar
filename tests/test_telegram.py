from __future__ import annotations

import json
import subprocess

from radar.telegram import TelegramClient, split_message

from .fakes import FakeHTTP, conn_error, fail, ok


def client(settings, http, **kw):
    sleeps: list[float] = []
    c = TelegramClient(settings, http=http, sleep=sleeps.append, **kw)
    return c, sleeps


def test_send_success_html_mode(settings):
    http = FakeHTTP([ok(42)])
    c, sleeps = client(settings, http)
    assert c.send("<b>hi</b>", buttons=[[("Replied", "s:replied:abc")]]) == 42
    method, payload = http.calls[0]
    assert method == "sendMessage"
    assert payload["parse_mode"] == "HTML"
    assert payload["chat_id"] == "4242"
    assert payload["reply_markup"]["inline_keyboard"][0][0] == {"text": "Replied", "callback_data": "s:replied:abc"}
    assert sleeps == []


def test_failure_then_retry_succeeds(settings):
    http = FakeHTTP([fail(), ok(7)])
    c, sleeps = client(settings, http)
    assert c.send("hello") == 7
    assert sleeps == [0] or len(sleeps) == 1
    assert not settings.delivery_failures_dir.exists() or not list(settings.delivery_failures_dir.iterdir())


def test_failure_retry_then_preserved(settings):
    http = FakeHTTP([conn_error(), conn_error()])
    c, sleeps = client(settings, http)
    assert c.send("<b>alert</b> text", buttons=[[("Skip", "s:skipped:abc")]], kind="alert") is None
    assert len(http.calls) == 2
    assert len(sleeps) == 1
    files = list(settings.delivery_failures_dir.glob("*_alert.txt"))
    assert len(files) == 1
    text, buttons, kind = TelegramClient.read_failure(files[0])
    assert text == "<b>alert</b> text"
    assert buttons == [[("Skip", "s:skipped:abc")]]
    assert kind == "alert"


def test_token_not_in_preserved_file_or_error(settings, caplog):
    http = FakeHTTP([conn_error(f"boom https://api.telegram.org/bot{settings.telegram_bot_token}/x")] * 2)
    c, _ = client(settings, http)
    c.send("x", kind="alert")
    body = next(settings.delivery_failures_dir.glob("*.txt")).read_text(encoding="utf-8")
    assert settings.telegram_bot_token not in body
    assert settings.telegram_bot_token not in caplog.text


def test_split_keeps_buttons_on_last_chunk(settings):
    long_text = "\n".join(f"line {i} " + "x" * 80 for i in range(120))
    assert len(long_text) > 8000
    http = FakeHTTP()
    c, _ = client(settings, http)
    assert c.send(long_text, buttons=[[("Replied", "s:replied:abc")]]) is not None
    payloads = [p for m, p in http.calls]
    assert len(payloads) >= 3
    assert all(len(p["text"]) <= 4000 for p in payloads)
    assert all("reply_markup" not in p for p in payloads[:-1])
    assert "reply_markup" in payloads[-1]


def test_split_message_rules():
    assert split_message("") == []
    assert split_message("short") == ["short"]
    chunks = split_message("word " * 2000, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert " ".join(chunks).split() == ("word " * 2000).split()


def test_resend_deletes_file(settings):
    c, _ = client(settings, FakeHTTP([conn_error(), conn_error()]))
    c.send("first", buttons=[[("Skip", "s:skipped:a")]], kind="alert")
    assert len(list(settings.delivery_failures_dir.glob("*.txt"))) == 1
    http = FakeHTTP()
    c2, _ = client(settings, http)
    assert c2.resend_failures() == (1, 0)
    assert list(settings.delivery_failures_dir.glob("*.txt")) == []
    assert http.calls[0][1]["text"] == "first"
    assert http.calls[0][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "s:skipped:a"


def test_resend_keeps_file_on_failure(settings):
    c, _ = client(settings, FakeHTTP([conn_error(), conn_error()]))
    c.send("first", kind="digest")
    c2, _ = client(settings, FakeHTTP([conn_error(), conn_error()]))
    assert c2.resend_failures() == (0, 1)
    assert len(list(settings.delivery_failures_dir.glob("*.txt"))) == 1


def test_resend_plain_text_file(settings):
    settings.delivery_failures_dir.mkdir(parents=True)
    (settings.delivery_failures_dir / "manual_note.txt").write_text("hand written", encoding="utf-8")
    http = FakeHTTP()
    c, _ = client(settings, http)
    assert c.resend_failures() == (1, 0)
    assert http.calls[0][1]["text"] == "hand written"


def test_not_configured_preserves_without_network(tmp_path):
    from .conftest import make_settings

    s = make_settings(tmp_path, TELEGRAM_BOT_TOKEN="REPLACE_ME")
    http = FakeHTTP()
    c, _ = client(s, http)
    assert c.send("hi", kind="alert") is None
    assert http.calls == []
    assert len(list(s.delivery_failures_dir.glob("*.txt"))) == 1


def test_dry_run_sends_nothing(settings, capsys):
    http = FakeHTTP()
    c, _ = client(settings, http, dry_run=True)
    assert c.send("hi") is None
    assert http.calls == []
    assert "[dry-run]" in capsys.readouterr().out
    assert not settings.delivery_failures_dir.exists()


def test_bad_html_falls_back_to_plain(settings):
    http = FakeHTTP([fail(400, "Bad Request: can't parse entities"), ok(9)])
    c, _ = client(settings, http)
    assert c.send("<b>bad <i>html</b>") == 9
    assert "parse_mode" not in http.calls[1][1]
    assert "<" not in http.calls[1][1]["text"]


def test_dns_failure_uses_curl_fallback(settings, monkeypatch):
    monkeypatch.setattr("radar.telegram.shutil.which", lambda name: "/usr/bin/curl")
    calls: list[list[str]] = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "nslookup":
            out = "Server:  one.one.one.one\nAddress:  1.1.1.1\n\nName: api.telegram.org\nAddresses: 149.154.166.110\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        body = json.dumps({"ok": True, "result": {"message_id": 55}})
        assert json.loads(kwargs["input"])["text"] == "hello"
        return subprocess.CompletedProcess(cmd, 0, stdout=body + "\n200", stderr="")

    http = FakeHTTP([conn_error("HTTPSConnectionPool: Failed to resolve 'api.telegram.org' (getaddrinfo failed)")])
    c = TelegramClient(settings, http=http, sleep=lambda s: None, runner=runner)
    assert c.send("hello") == 55
    curl_cmd = calls[1]
    assert "api.telegram.org:443:149.154.166.110" in curl_cmd
    # Later calls go straight to curl, no second plain attempt.
    assert c.send("hello") == 55
    assert len(http.calls) == 1
