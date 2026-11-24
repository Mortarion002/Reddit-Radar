"""A fake imaplib.IMAP4_SSL for mailbox tests."""

from __future__ import annotations

import imaplib
from dataclasses import dataclass, field


@dataclass
class FakeIMAP:
    messages: dict[str, bytes] = field(default_factory=dict)
    fail_connect: bool = False
    fail_login: bool = False
    commands: list[tuple] = field(default_factory=list)
    readonly: bool | None = None
    logged_out: bool = False

    def factory(self, host, port, timeout=None):
        self.commands.append(("CONNECT", host, port))
        if self.fail_connect:
            raise OSError("[Errno 11001] getaddrinfo failed")
        return self

    def login(self, user, password):
        self.commands.append(("LOGIN", user))
        if self.fail_login:
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
        return "OK", [b"Logged in"]

    def select(self, folder, readonly=False):
        self.readonly = readonly
        self.commands.append(("SELECT", folder, readonly))
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command, *args):
        command = command.upper()
        self.commands.append((command, *args))
        if command == "SEARCH":
            return "OK", [" ".join(self.messages).encode()]
        if command == "FETCH":
            uid, spec = args
            raw = self.messages.get(uid)
            if raw is None:
                return "OK", [None]
            return "OK", [(f"1 (UID {uid} BODY[] {{{len(raw)}}}".encode(), raw), b")"]
        if command == "STORE":
            return "OK", [b"1 (FLAGS (\\Seen))"]
        raise AssertionError(f"unexpected IMAP command {command}")

    def close(self):
        self.commands.append(("CLOSE",))

    def logout(self):
        self.logged_out = True
        self.commands.append(("LOGOUT",))

    def of(self, name: str) -> list[tuple]:
        return [c for c in self.commands if c[0] == name]
