"""Fakes for Telegram HTTP and the Telegram client."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import requests


@dataclass
class FakeResponse:
    status_code: int
    payload: dict[str, Any]

    @property
    def text(self) -> str:
        return json.dumps(self.payload)


@dataclass
class FakeHTTP:
    """Stands in for the `requests` module. `script` is a list of responses or exceptions, used in order."""

    script: list[Any] = field(default_factory=list)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    next_id: int = 100

    def post(self, url: str, json: dict[str, Any] | None = None, timeout: float | None = None) -> FakeResponse:
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, json or {}))
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        self.next_id += 1
        return FakeResponse(200, {"ok": True, "result": {"message_id": self.next_id}})

    def sent_texts(self) -> list[str]:
        return [p.get("text", "") for m, p in self.calls if m == "sendMessage"]


def ok(message_id: int = 1) -> FakeResponse:
    return FakeResponse(200, {"ok": True, "result": {"message_id": message_id}})


def fail(status: int = 500, description: str = "Internal Server Error") -> FakeResponse:
    return FakeResponse(status, {"ok": False, "description": description})


def conn_error(msg: str = "Connection refused") -> requests.exceptions.ConnectionError:
    return requests.exceptions.ConnectionError(msg)


@dataclass
class FakeTelegram:
    """Records sends; used where the pipeline only needs a client-shaped object."""

    sent: list[dict[str, Any]] = field(default_factory=list)
    fail: bool = False
    resends: int = 0
    configured: bool = True
    _next: int = 1000

    def send(self, text, buttons=None, kind="message", silent=False, preserve=True, meta=None):
        self.sent.append({"text": text, "buttons": buttons, "kind": kind, "silent": silent, "meta": meta})
        if self.fail:
            return None
        self._next += 1
        return self._next

    updates: list[dict[str, Any]] = field(default_factory=list)
    offsets: list[Any] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)
    edits: list[tuple[str, int, Any]] = field(default_factory=list)

    def resend_failures(self, on_sent=None):
        self.resends += 1
        return 0, 0

    def get_updates(self, offset):
        self.offsets.append(offset)
        return [u for u in self.updates if offset is None or u["update_id"] >= offset]

    def answer_callback(self, callback_id, text):
        self.answers.append(text)
        return True

    def edit_buttons(self, chat_id, message_id, buttons):
        self.edits.append((chat_id, message_id, buttons))
        return True

    def kinds(self) -> list[str]:
        return [s["kind"] for s in self.sent]
