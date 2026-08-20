"""Doubles for the cloud-password dispatch tests — shared by two modules.

``test_twofa.py`` owns the request SHAPE (which verb sends what, and what reaches
the wire) and ``test_twofa_srp.py`` owns HOW the SRP work is called (off the loop
thread, bounded, and with the client salt extended). They need the same raw
client, so it lives here rather than in two drifting copies.

The client is RAW on purpose: it answers requests and has no ``edit_2fa`` method
to fall back on. A ``**kwargs`` recorder asserts only what the caller passed in,
and the previous double's ``edit_2fa`` issued no internal ``getPassword`` — which
is exactly how a pre-flight read that closed half its window stayed green.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from telethon.tl.functions.account import GetPasswordRequest, UpdatePasswordSettingsRequest
from telethon.tl.types import PasswordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow

if TYPE_CHECKING:
    import pytest

PASSWORD = "s3cret-passphrase"
SERVER_SALT = b"server-chose-this"
DIGEST = b"new-password-digest"
PROOF = "srp-proof"
# ``telethon.password``'s own client-salt extension, asserted rather than trusted.
CLIENT_SALT_BYTES = 32


def algo() -> PasswordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow:
    """A fresh ``new_algo`` per call: the dispatcher MUTATES ``salt1`` in place."""
    return PasswordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow(
        salt1=SERVER_SALT,
        salt2=b"salt2",
        g=3,
        p=b"\x01" * 256,
    )


class Password:
    """Just the ``account.Password`` attributes the dispatcher reads.

    Deliberately not a ``MagicMock``: a mock answers every attribute with another
    mock, which is exactly the shape ``_flag`` / ``_text`` must reject, so a mock
    would hide the coercion instead of exercising it.
    """

    def __init__(self, **fields: object) -> None:
        self.new_algo = algo()
        for name, value in fields.items():
            setattr(self, name, value)


class PasswordClient:
    """Answers ``GetPasswordRequest`` with one canned reply; records every request."""

    def __init__(self, reply: object) -> None:
        self._reply = reply
        self.requests: list[object] = []

    async def connect(self) -> None:
        return None

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        assert isinstance(request, GetPasswordRequest)
        return self._reply


class RawClient:
    """The password path's two RPCs and nothing else.

    ``read_error`` fails the ``getPassword`` leg and ``error`` fails the write leg,
    and the split is the whole classification: everything before the read cannot have
    left the process, everything after it can.
    """

    def __init__(
        self,
        *,
        password: object | None = None,
        read_error: Exception | None = None,
        error: Exception | None = None,
    ) -> None:
        self._password = password if password is not None else Password(has_password=True)
        self._read_error = read_error
        self._error = error
        self.requests: list[object] = []

    async def connect(self) -> None:
        return None

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        if isinstance(request, GetPasswordRequest):
            if self._read_error is not None:
                raise self._read_error
            return self._password
        assert isinstance(request, UpdatePasswordSettingsRequest), "only two RPCs are allowed"
        if self._error is not None:
            raise self._error
        return True

    def written(self) -> Any:
        matched = [r for r in self.requests if isinstance(r, UpdatePasswordSettingsRequest)]
        assert len(matched) == 1, f"expected exactly one write, got {len(matched)}"
        return matched[0]


def patch_srp(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Replace both SRP computations with sentinels; record what they were asked to do.

    They are Telethon's own implementation over 2048-bit modular arithmetic and need
    a live server challenge, so no test computes a real one. The recorded thread
    ident is what ``test_twofa_srp.py`` reads.
    """
    calls: dict[str, list[Any]] = {"check": [], "digest": [], "threads": []}

    def _check(pwd: object, password: str) -> str:
        calls["check"].append((pwd, password))
        calls["threads"].append(threading.get_ident())
        return PROOF

    def _digest(kdf: object, password: str) -> bytes:
        calls["digest"].append((kdf, password))
        calls["threads"].append(threading.get_ident())
        return DIGEST

    monkeypatch.setattr("core.telegram_client._twofa.compute_check", _check)
    monkeypatch.setattr("core.telegram_client._twofa.compute_digest", _digest)
    return calls
