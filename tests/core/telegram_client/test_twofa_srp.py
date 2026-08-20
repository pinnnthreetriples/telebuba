"""How the SRP work is CALLED — admitted, off the loop thread, off the DB pool, bounded.

Its own module because ``test_twofa.py`` owns the request shapes and is at the
700-line test-source cap. What is here is not about which fields a verb sends; it
is about the properties of ``compute_check`` / ``compute_digest`` that a wedged
uvicorn worker, a starved database pool and a weakened KDF turned into
requirements:

- only the ``(p, g)`` pair Telethon's ``check_prime_and_good`` short-circuits on
  is ever computed against, so the non-terminating arm is UNREACHABLE rather than
  merely bounded — and that pair is verified against Telethon itself, not assumed;
- the client salt is extended with 32 random bytes, or every password this
  dashboard sets is keyed by a salt the server alone chose;
- neither computation runs on the event-loop thread, and neither runs on the
  loop's DEFAULT executor, which is the pool every database call in this app uses;
- neither can run forever.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from telethon.password import check_prime_and_good
from telethon.tl.functions.account import GetPasswordRequest

from core.telegram_client import execute
from core.telegram_client._twofa_srp import (
    _GOOD_G,
    _GOOD_PRIME,
    _SRP_EXECUTOR,
    _SRP_TIMEOUT_SECONDS,
    TwoFactorGatewayError,
    require_fast_algo,
)
from schemas.telegram_actions import ManageTwoFactorEmail, SetTwoFactorPassword
from tests.core.telegram_client._twofa_doubles import (
    CLIENT_SALT_BYTES,
    FOREIGN_PRIME,
    PASSWORD,
    SERVER_SALT,
    Password,
    RawClient,
    algo,
    patch_srp,
)
from tests.core.telegram_client.helpers import patch_action_client

# Telethon's fast path is a byte compare plus a set membership, so it answers in
# microseconds. Anything that reaches ``check_prime_and_good_check`` starts
# factorising a 2048-bit prime and never comes back, so a bound this loose still
# tells the two apart with no ambiguity.
_FAST_PATH_DEADLINE_SECONDS = 2.0
# The client-salt extension has to be sampled AT CALL TIME. ``kdf`` is the live algo
# object the dispatcher mutates in place, so reading ``kdf.salt1`` after the fact
# reports the post-mutation value either way — which is how a digest computed BEFORE
# the extension would still have looked correct.
_SALT_AT_CALL_TIME = "salt-at-call-time"


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
    # The digest is computed against the EXTENDED algo, and this reads the SNAPSHOT
    # the stub took inside the call rather than the object afterwards: the dispatcher
    # mutates ``salt1`` in place, so a post-hoc read would agree with the wire even
    # for a digest computed BEFORE the extension.
    assert srp[_SALT_AT_CALL_TIME] == salts


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
    monkeypatch.setattr("core.telegram_client._twofa_srp._SRP_TIMEOUT_SECONDS", 0.05)
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

    Same ``compute_check``, same server-supplied challenge, same bound — so the
    recovery-email flows had the same power to wedge the worker, and the fix is only a
    fix if both halves share it. The admission check above is what makes the
    non-terminating input unreachable on both; this is the layer underneath it.
    """
    release = threading.Event()

    def _never_returns(*_args: object) -> object:
        release.wait(30)
        return "far too late"

    monkeypatch.setattr("core.telegram_client._twofa_email.compute_check", _never_returns)
    monkeypatch.setattr("core.telegram_client._twofa_srp._SRP_TIMEOUT_SECONDS", 0.05)
    # The email path reads ``current_algo`` rather than ``has_password``.
    client = RawClient(password=Password(has_password=True))
    patch_action_client(monkeypatch, client)
    action = ManageTwoFactorEmail(mode="set", current_password=PASSWORD, email="r@example.com")

    try:
        result = await execute("acc-mail", action)
    finally:
        release.set()

    assert result.status == "failed"
    assert result.error_message == "twofa_password_compute_timeout"
    assert [type(request) for request in client.requests] == [GetPasswordRequest]


@pytest.mark.parametrize("generator", sorted(_GOOD_G))
def test_the_admitted_pair_is_exactly_the_one_telethon_short_circuits_on(
    generator: int,
) -> None:
    """The empirical half: our copy of the prime is Telethon's, verified against it.

    ``check_prime_and_good``'s fast path is a byte compare against a prime that lives
    as a LOCAL inside that function, so the copy in ``_twofa_srp`` cannot be imported
    and has to be asserted. Handing it to Telethon proves byte equality with no
    second copy: a prime differing by one byte falls through to Pollard-Brent
    factorisation of a 2048-bit prime and never returns, so "it came back at all" is
    the assertion. Run in a worker with a hard deadline, because the failure mode of
    a wrong constant is a hang rather than a wrong answer.
    """
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        # ``result`` raises ``TimeoutError`` rather than blocking the suite, and the
        # pool is torn down WITHOUT waiting — a context manager would join the worker
        # on the way out and turn a clean failure back into the hang this is testing
        # for. The daemon-less worker is left behind only on failure, which is the
        # same trade the production bound makes.
        assert (
            pool.submit(
                check_prime_and_good,
                _GOOD_PRIME,
                generator,
            ).result(timeout=_FAST_PATH_DEADLINE_SECONDS)
            is None
        )
    finally:
        pool.shutdown(wait=False)


@pytest.mark.parametrize(
    ("prime", "generator", "reason"),
    [
        pytest.param(FOREIGN_PRIME, 3, "a rotated prime", id="foreign-prime"),
        # The correct prime is NOT sufficient. Telethon's fast path is nested inside
        # the byte compare, so a ``g`` outside its four falls through to the same
        # factorisation — 2 and 6 are perfectly ordinary Telegram generators.
        pytest.param(_GOOD_PRIME, 2, "a generator outside the four", id="good-prime-g2"),
        pytest.param(_GOOD_PRIME, 6, "a generator outside the four", id="good-prime-g6"),
    ],
)
def test_anything_outside_that_pair_is_refused_without_being_computed(
    prime: bytes,
    generator: int,
    reason: str,
) -> None:
    """The gate, asserted directly and WITHOUT calling Telethon — that call never ends.

    This is what makes the timeout below defence in depth rather than the only line
    of defence: for every input that would not terminate, nothing is offloaded at
    all, so there is no thread to leak and no request to lose.
    """
    with pytest.raises(TwoFactorGatewayError) as excinfo:
        require_fast_algo(algo(p=prime, g=generator))

    assert excinfo.value.code == "twofa_password_algo_unsupported", reason


@pytest.mark.asyncio
async def test_a_rotated_prime_is_refused_before_any_computation_happens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end: the gate fires inside the dispatcher, so the write is never reached.

    A ``compute_digest`` that fails the test if it is called at all is the only honest
    stub here — the real one would not return, and the point of the gate is that it
    is not reached.
    """

    def _must_not_run(*_args: object) -> bytes:
        pytest.fail("the SRP work ran for a prime Telethon cannot validate")

    monkeypatch.setattr("core.telegram_client._twofa.compute_digest", _must_not_run)
    client = RawClient(password=Password(new_algo=algo(p=FOREIGN_PRIME)))
    patch_action_client(monkeypatch, client)

    result = await execute("acc-2fa", SetTwoFactorPassword(new_password=PASSWORD))

    assert result.status == "failed"
    assert result.error_type == "TwoFactorGatewayError"
    assert result.error_message == "twofa_password_algo_unsupported"
    assert [type(request) for request in client.requests] == [GetPasswordRequest]


@pytest.mark.asyncio
async def test_the_srp_work_stays_off_the_pool_every_database_call_uses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``asyncio.to_thread`` would have run this on the loop's DEFAULT executor.

    That is the pool ~450 ``core.repositories`` call sites use through
    ``asyncio.to_thread``, none of them bounded. Measured on that arrangement: 20
    expiries fill the executor and an ordinary repository call never runs; four
    leaked threads take event-loop lateness from 11 ms to 182 ms; and
    ``asyncio.run``'s ``shutdown_default_executor`` JOINS the leaked worker, so the
    process hangs instead of exiting. A private pool keeps all three off the DB.
    """
    srp = patch_srp(monkeypatch)
    patch_action_client(monkeypatch, RawClient())
    default_pool_thread = await asyncio.to_thread(threading.current_thread)

    result = await execute(
        "acc-2fa",
        SetTwoFactorPassword(current_password="old", new_password=PASSWORD),
    )

    assert result.status == "ok"
    names = srp["thread_names"]
    assert len(names) == 2
    assert all(name.startswith("twofa-srp") for name in names), names
    assert default_pool_thread.name not in names
    assert _SRP_EXECUTOR._max_workers == 2  # the bound IS the contract


def test_the_srp_bound_is_generous_enough_for_the_measured_cost() -> None:
    """The constant itself, which every other test monkeypatches away.

    The happy path is 68 ms + 98 ms of pure-Python modular arithmetic, so the bound
    has to clear that by a wide margin on a loaded box — and it has to stay well
    under the operator-visible request timeout, or it would stop being the thing that
    turns a wedged worker into one failed request.
    """
    assert 1.0 <= _SRP_TIMEOUT_SECONDS <= 30.0


@pytest.mark.asyncio
async def test_the_proof_is_computed_against_the_password_object_telegram_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``compute_check(pwd, ...)`` — the FIRST argument, which no assertion had pinned.

    Every other test unpacks that tuple and discards it, so passing the wrong object
    (the algo, a stale read, ``None``) killed no test. It matters: ``compute_check``
    reads ``current_algo`` AND ``srp_B`` AND ``srp_id`` off it, and the SRP challenge
    is single-use, so a proof built against anything but this read is refused by
    Telegram as an invalid password.
    """
    srp = patch_srp(monkeypatch)
    live = Password(has_password=True)
    client = RawClient(password=live)
    patch_action_client(monkeypatch, client)

    result = await execute(
        "acc-2fa",
        SetTwoFactorPassword(current_password="old", new_password=PASSWORD),
    )

    assert result.status == "ok"
    assert [pwd for pwd, _password in srp["check"]] == [live]
    # The digest takes the ALGO off that same read, not the ``Password`` wrapper.
    assert [kdf for kdf, _password in srp["digest"]] == [live.new_algo]
