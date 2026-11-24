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

    def send(self, text, buttons=None, kind="message", silent=False, preserve=True):
        self.sent.append({"text": text, "buttons": buttons, "kind": kind, "silent": silent})
        if self.fail:
            return None
        self._next += 1
        return self._next

    def resend_failures(self):
        self.resends += 1
        return 0, 0

    def kinds(self) -> list[str]:
        return [s["kind"] for s in self.sent]
