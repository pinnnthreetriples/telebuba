"""Doubles for the cloud-password dispatch tests — shared by two modules.

``test_twofa.py`` owns the request SHAPE (which verb sends what, and what reaches
the wire) and ``test_twofa_srp.py`` owns HOW the SRP work is called (only for an
admitted ``(p, g)``, off the loop thread, off the database pool, bounded, and with
the client salt extended). They need the same raw client, so it lives here rather
than in two drifting copies.

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

from core.telegram_client._twofa_srp import _GOOD_PRIME

if TYPE_CHECKING:
    import pytest

PASSWORD = "s3cret-passphrase"
SERVER_SALT = b"server-chose-this"
DIGEST = b"new-password-digest"
PROOF = "srp-proof"
# ``telethon.password``'s own client-salt extension, asserted rather than trusted.
CLIENT_SALT_BYTES = 32
# A 2048-bit prime that is NOT Telethon's: RFC 3526 group 14, the one the
# non-termination was measured on, so it is the realistic "Telegram rotated its
# prime" input rather than an arbitrary blob.
FOREIGN_PRIME = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74"
    "020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F1437"
    "4FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF05"
    "98DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB"
    "9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF695581718"
    "3995497CEA956AE515D2261898FA051015728E5A8AACAA68FFFFFFFFFFFFFFFF",
    16,
).to_bytes(256, "big")


def algo(
    *,
    p: bytes = _GOOD_PRIME,
    g: int = 3,
) -> PasswordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow:
    """A fresh ``new_algo`` per call: the dispatcher MUTATES ``salt1`` in place.

    ``p`` / ``g`` default to the ONE pair ``telethon.password.check_prime_and_good``
    short-circuits on, because that pair is now an ADMISSION requirement rather than
    an implementation detail: anything else is refused before a digest is computed,
    so a placeholder ``p`` would make every write test refuse instead of write.
    """
    return PasswordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow(
        salt1=SERVER_SALT,
        salt2=b"salt2",
        g=g,
        p=p,
    )


class Password:
    """Just the ``account.Password`` attributes the dispatcher reads.

    Deliberately not a ``MagicMock``: a mock answers every attribute with another
    mock, which is exactly the shape ``_flag`` / ``_text`` must reject, so a mock
    would hide the coercion instead of exercising it.

    ``current_algo`` is present by default and is a REAL algorithm: an account with
    a password has one, ``compute_check`` reads it straight off this object, and a
    double without it is what hid the missing-flag guard the password path had
    forgotten. Pass ``current_algo=None`` to get the account that has none.
    """

    def __init__(self, **fields: object) -> None:
        self.new_algo = algo()
        self.current_algo = algo()
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
        rereads: tuple[object, ...] = (),
    ) -> None:
        self._password = password if password is not None else Password(has_password=True)
        self._read_error = read_error
        self._error = error
        # Answers for the reads AFTER the first one, in order. Only the confirming
        # ``getPassword`` an ``EMAIL_UNCONFIRMED`` triggers gets that far, and it is
        # the whole point of that path that its answer can differ from the opening
        # read — or be an exception, which is raised rather than returned.
        self._rereads = list(rereads)
        self.requests: list[object] = []

    async def connect(self) -> None:
        return None

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        if isinstance(request, GetPasswordRequest):
            if self._read_error is not None:
                raise self._read_error
            if self.reads() > 1 and self._rereads:
                answer = self._rereads.pop(0)
                if isinstance(answer, Exception):
                    raise answer
                return answer
            return self._password
        assert isinstance(request, UpdatePasswordSettingsRequest), "only two RPCs are allowed"
        if self._error is not None:
            raise self._error
        return True

    def reads(self) -> int:
        return sum(isinstance(r, GetPasswordRequest) for r in self.requests)

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
    calls: dict[str, list[Any]] = {
        "check": [],
        "digest": [],
        "threads": [],
        # Which POOL ran it, not just which thread: ``asyncio.to_thread`` and a
        # private executor both leave the loop thread, so the ident alone cannot tell
        # "off the loop" from "on the pool every database call shares".
        "thread_names": [],
        # ``salt1`` SNAPSHOTTED at call time. The dispatcher mutates the algo in
        # place, so reading it afterwards reports the post-extension value even for a
        # digest that ran before the extension.
        "salt-at-call-time": [],
    }

    def _record(thread_names: list[Any]) -> None:
        calls["threads"].append(threading.get_ident())
        thread_names.append(threading.current_thread().name)

    def _check(pwd: object, password: str) -> str:
        calls["check"].append((pwd, password))
        _record(calls["thread_names"])
        return PROOF

    def _digest(kdf: object, password: str) -> bytes:
        calls["digest"].append((kdf, password))
        calls["salt-at-call-time"].append(bytes(kdf.salt1))  # ty: ignore[unresolved-attribute]
        _record(calls["thread_names"])
        return DIGEST

    monkeypatch.setattr("core.telegram_client._twofa.compute_check", _check)
    monkeypatch.setattr("core.telegram_client._twofa.compute_digest", _digest)
    return calls
