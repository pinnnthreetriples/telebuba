"""How the SRP work is CALLED — off the loop thread, bounded, and salted.

Its own module because ``test_twofa.py`` owns the request shapes and is at the
700-line test-source cap. What is here is not about which fields a verb sends; it
is about the three properties of ``compute_check`` / ``compute_digest`` that a
wedged uvicorn worker and a weakened KDF turned into requirements:

- the client salt is extended with 32 random bytes, or every password this
  dashboard sets is keyed by a salt the server alone chose;
- neither computation runs on the event-loop thread;
- neither can run forever, because ``check_prime_and_good`` genuinely does not
  terminate for a prime that is not Telethon's hardcoded one.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from telethon.tl.functions.account import GetPasswordRequest

from core.telegram_client import execute
from schemas.telegram_actions import ManageTwoFactorEmail, SetTwoFactorPassword
from tests.core.telegram_client._twofa_doubles import (
    CLIENT_SALT_BYTES,
    PASSWORD,
    SERVER_SALT,
    Password,
    RawClient,
    patch_srp,
)
from tests.core.telegram_client.helpers import patch_action_client


@pytest.mark.asyncio
async def test_the_client_salt_is_extended_with_fresh_random_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pwd.new_algo.salt1 += os.urandom(32)``, and it is not decoration.

    The server chooses ``salt1``; appending 32 bytes of client randomness is what
    keeps the KDF from being keyed by a salt the server alone controls. Telethon does
    it, and an implementation that "simplified" it away would weaken every password
    this dashboard sets without changing one visible behaviour — so it is asserted on
    the wire, and asserted to DIFFER between two calls.
    """
    srp = patch_srp(monkeypatch)
    salts: list[bytes] = []
    for _attempt in range(2):
        client = RawClient()
        patch_action_client(monkeypatch, client)
        result = await execute("acc-2fa", SetTwoFactorPassword(new_password=PASSWORD))
        assert result.status == "ok"
        salts.append(client.written().new_settings.new_algo.salt1)

    for salt in salts:
        assert salt.startswith(SERVER_SALT)
        assert len(salt) == len(SERVER_SALT) + CLIENT_SALT_BYTES
    assert salts[0] != salts[1], "the appended bytes must be random, not a constant"
    # The digest is computed against the EXTENDED algo, not the server's original.
    assert [kdf.salt1 for kdf, _password in srp["digest"]] == salts


@pytest.mark.asyncio
async def test_the_srp_work_never_runs_on_the_event_loop_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """68 ms + 98 ms of pure-Python modular arithmetic, off the one loop this app has.

    Measured: a change spends 165 ms in ``compute_check`` + ``compute_digest``, and a
    heartbeat on the loop thread came back 171 ms late. One operator action could
    afford that; the reason it is a hard requirement rather than a nicety is the
    bounded-timeout sibling below, which can only work from a thread.
    """
    srp = patch_srp(monkeypatch)
    client = RawClient()
    patch_action_client(monkeypatch, client)
    loop_thread = threading.get_ident()

    result = await execute(
        "acc-2fa",
        SetTwoFactorPassword(current_password="old", new_password=PASSWORD),
    )

    assert result.status == "ok"
    assert len(srp["threads"]) == 2
    assert loop_thread not in srp["threads"]
    assert asyncio.get_running_loop().is_running()


@pytest.mark.asyncio
async def test_an_endless_prime_check_fails_one_request_instead_of_the_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CRITICAL one. ``check_prime_and_good`` does not always terminate.

    Its fast path fires only when ``algo.p`` byte-equals Telethon's hardcoded prime;
    any other ``p`` falls into Pollard-Brent factorisation of a prime, which does not
    finish — measured on the RFC 3526 group-14 prime, still running after 30 s. ``p``
    is SERVER-supplied, so before the bound a Telegram prime rotation wedged the
    single uvicorn worker for good: warming, the listener, SSE and ``/ready`` with
    it. The bound converts that into this — one failed request carrying a stable
    code, nothing written, and a loop still answering.
    """
    release = threading.Event()

    def _never_returns(*_args: object) -> bytes:
        release.wait(30)
        return b"far too late"

    monkeypatch.setattr("core.telegram_client._twofa.compute_digest", _never_returns)
    monkeypatch.setattr("core.telegram_client._twofa._SRP_TIMEOUT_SECONDS", 0.05)
    client = RawClient()
    patch_action_client(monkeypatch, client)

    try:
        result = await execute("acc-2fa", SetTwoFactorPassword(new_password=PASSWORD))
    finally:
        # The thread cannot be killed — Python offers no way to — so the test releases
        # it rather than leaving the suite to join a 30-second sleep at exit. In
        # production that thread is exactly the leak this bound trades for a live
        # worker, which is what the module comment says out loud.
        release.set()

    assert result.status == "failed"
    assert result.error_type == "TwoFactorGatewayError"
    assert result.error_message == "twofa_password_compute_timeout"
    assert [type(request) for request in client.requests] == [GetPasswordRequest]
    assert asyncio.get_running_loop().is_running()


@pytest.mark.asyncio
async def test_an_unusable_srp_challenge_becomes_one_stable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``compute_check`` / ``compute_digest`` say everything with a bare ``ValueError``.

    Unimplemented algorithm class, bad p/g, bad B, bad g_b — four different messages,
    all of them Telethon internals, none of them an ``RPCError``. Collapsed into one
    code by ``_srp``, which is also what keeps it apart from the timeout above: the
    wrapper IS a ``ValueError``, so mapping this at the call site would relabel one
    as the other.
    """

    def _unusable(*_args: object) -> object:
        msg = "bad p/g in password"
        raise ValueError(msg)

    monkeypatch.setattr("core.telegram_client._twofa.compute_digest", _unusable)
    client = RawClient()
    patch_action_client(monkeypatch, client)

    result = await execute("acc-2fa", SetTwoFactorPassword(new_password=PASSWORD))

    assert result.status == "failed"
    assert result.error_type == "TwoFactorGatewayError"
    assert result.error_message == "twofa_password_algo_unsupported"
    # Refused before the write, so nothing about the account changed.
    assert [type(request) for request in client.requests] == [GetPasswordRequest]


@pytest.mark.asyncio
async def test_the_email_path_runs_its_proof_through_the_same_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``updatePasswordSettings`` for a recovery email needs the identical proof.

    Same ``compute_check``, same server-supplied prime, same non-terminating check —
    so the recovery-email flows had the same power to wedge the worker, and the fix
    is only a fix if both halves share it.
    """
    release = threading.Event()

    def _never_returns(*_args: object) -> object:
        release.wait(30)
        return "far too late"

    monkeypatch.setattr("core.telegram_client._twofa_email.compute_check", _never_returns)
    monkeypatch.setattr("core.telegram_client._twofa._SRP_TIMEOUT_SECONDS", 0.05)
    # The email path reads ``current_algo`` rather than ``has_password``.
    client = RawClient(password=Password(has_password=True, current_algo=object()))
    patch_action_client(monkeypatch, client)
    action = ManageTwoFactorEmail(mode="set", current_password=PASSWORD, email="r@example.com")

    try:
        result = await execute("acc-mail", action)
    finally:
        release.set()

    assert result.status == "failed"
    assert result.error_message == "twofa_password_compute_timeout"
    assert [type(request) for request in client.requests] == [GetPasswordRequest]
