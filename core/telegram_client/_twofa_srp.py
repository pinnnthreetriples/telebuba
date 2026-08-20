"""The SRP compute hazard: which challenges are safe to compute, and the bound behind it.

Extracted sibling of ``_twofa`` (440-line file budget) holding the four things the
cloud-password paths share and nothing else: the refusal class, the KDF class
Telethon implements, the ``(p, g)`` admission check, and the bounded worker. Both
``_twofa`` and ``_twofa_email`` import from here; nothing here imports either.

**Why an admission check exists at all.** ``compute_digest`` and ``compute_check``
both open with ``telethon.password.check_prime_and_good``, whose fast path is (read
off ``telethon/password.py``, 1.44):

    if good_prime == prime_bytes:
        if g in (3, 4, 5, 7):
            return  # It's good
    check_prime_and_good_check(int.from_bytes(prime_bytes, 'big'), g)

so it short-circuits ONLY for the hardcoded prime AND only for those four
generators — the correct prime with ``g`` of 2 or 6 falls through as well. The
fall-through runs ``factorization.Factorization.factorize`` (Pollard-Brent) over a
2048-bit PRIME, which does not terminate: measured on the RFC 3526 group-14 prime,
still running after 30 s. ``p`` and ``g`` are SERVER-supplied.

So the pair is checked BEFORE anything is offloaded (:func:`require_fast_algo`) and
anything else is refused outright. That is what makes the non-terminating path
unreachable rather than merely bounded.

**Why the bound is still here, and why it has its own pool.** Defence in depth for
the case where a future Telethon changes that fast path under us. It used to run on
``asyncio.to_thread``, i.e. the loop's DEFAULT executor — which is the same pool
every ``core.repositories`` call runs on (see ``DbSettings.pool_size``, ~450
``to_thread`` call sites, none of them bounded). Measured on that arrangement: 20
expiries fill the executor and an ordinary repository call never runs; four leaked
threads take event-loop lateness from 11 ms to 182 ms (it is the GIL, not one
core); and ``asyncio.run``'s ``shutdown_default_executor`` JOINS the leaked worker,
so the process hangs at shutdown instead of exiting. A private two-worker pool
keeps all three off the database.

What the bound cannot do is stop the spinning: Python offers no way to kill a
thread. An expiry leaks one worker out of two, and the honest residual is that
interpreter exit (``concurrent.futures``' own ``atexit`` join) would then wait on
it — one process-exit hang rather than a wedged worker, a starved database pool and
a hung ``asyncio.run``.

ponytail: two PRE-EXISTING shutdown residuals were measured next to this one and
are out of scope here — a live SSE subscriber blocks graceful shutdown
indefinitely, and once a Telethon client has been borrowed from the pool, shutdown
leaves five pending Telethon tasks. Neither is caused by this module; both are
recorded because they are what a shutdown hang here would be confused with.

Happy-path cost, MEASURED: 68 ms for a ``compute_digest``, 98 ms for a
``compute_check``, 165 ms for a change doing both, during which a heartbeat on the
loop thread came back 171 ms late. (An earlier comment guessed "~1s".) That is why
it runs off the loop thread at all.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from telethon.tl.types import PasswordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow

if TYPE_CHECKING:
    from collections.abc import Callable

# The one KDF class Telethon implements. ``passwordKdfAlgoUnknown`` is the other member
# of that TL union and carries no salt at all, so it is refused rather than reached.
_ModPowAlgo = PasswordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow


class TwoFactorGatewayError(ValueError):
    """A cloud-password action was refused; ``str(exc)`` is the stable code.

    Same contract as :class:`core.telegram_client._media.ProfileGatewayError`: the
    code rides ``execute``'s generic-exception ladder into
    ``ActionResult.error_message`` verbatim and the SPA translates it. The unreadable
    detail travels as the chained cause into the failure log — for this family the
    only place any Telethon text about the attempt exists.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# Telethon's hardcoded ``good_prime``, copied byte for byte rather than imported
# because it is a local inside ``check_prime_and_good``. It is not trusted on faith:
# ``test_twofa_srp.py`` hands this value to Telethon's own checker and asserts the
# call returns inside two seconds, which only the byte-equal fast path can do.
_GOOD_PRIME = bytes.fromhex(
    "c71caeb9c6b1c9048e6c522f70f13f73980d40238e3e21c14934d037563d930f"
    "48198a0aa7c14058229493d22530f4dbfa336f6e0ac925139543aed44cce7c37"
    "20fd51f69458705ac68cd4fe6b6b13abdc9746512969328454f18faf8c595f64"
    "2477fe96bb2a941d5bcd1d4ac8cc49880708fa9b378e3c4f3a9060bee67cf9a4"
    "a4a695811051907e162753b56b0f6b410dba74d8a84b2a14b3144e0ef1284754"
    "fd17ed950d5965b4b9dd46582db1178d169c6bc465b0d6ff9ca3928fef5b9ae4"
    "e418fc15e83ebea0f87fa9ff5eed70050ded2849f47bf959d956850ce929851f"
    "0d8115f635b105ee2e4e15d04b2454bf6f4fadf034b10403119cd8e3b92fcc5b",
)
# The generators that fast path accepts for that prime. 2 and 6 are valid Telegram
# generators and are NOT here, because Telethon sends them down the factorisation
# arm just like a foreign prime would.
_GOOD_G = frozenset({3, 4, 5, 7})
_ALGO_UNSUPPORTED = "twofa_password_algo_unsupported"


def require_fast_algo(algo: object) -> _ModPowAlgo:
    """The KDF, or a refusal — the gate that keeps the non-terminating path unreachable.

    Called on every algorithm before it reaches :func:`_srp`, for both the
    ``new_algo`` a write extends and the ``current_algo`` a proof is computed
    against. The refusal is deliberately the same stable code Telethon's own
    ``ValueError`` would have produced, because from the operator's side it is the
    same fact: this build cannot use the challenge Telegram sent.
    """
    if not isinstance(algo, _ModPowAlgo):
        # ``passwordKdfAlgoUnknown``: the server offered a KDF this Telethon cannot
        # implement, so there is no salt to extend and no digest to compute.
        raise TwoFactorGatewayError(_ALGO_UNSUPPORTED)
    if bytes(algo.p) != _GOOD_PRIME or algo.g not in _GOOD_G:
        raise TwoFactorGatewayError(_ALGO_UNSUPPORTED)
    return algo


_SRP_TIMEOUT_SECONDS = 15.0
# NOT the loop's default executor — see the module docstring for what sharing it
# with every database call cost. Two workers: a set/change does at most two
# computations and they are sequential, so this is one spare, not a queue.
_SRP_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="twofa-srp")


async def _srp[T](compute: Callable[..., T], *args: object) -> T:
    """One SRP computation, off the loop thread and bounded — see the module docstring.

    Every refusal collapses into a stable code here rather than at the call sites.
    ``ValueError`` is ``compute_check`` / ``compute_digest``'s vocabulary for a
    challenge they cannot use (an unimplemented algorithm class, a bad p/g, a bad B
    or g_b); ``AttributeError`` is what they raise for a ``Password`` missing an
    optional TL flag entirely — ``compute_check`` opens with ``request.current_algo``
    (``telethon/password.py:137``) and every field on ``account.Password`` is
    optional. Neither is actionable prose.

    Doing it here also keeps the two apart: ``TwoFactorGatewayError`` IS a
    ``ValueError``, so a call site wrapping this in ``except ValueError`` would
    relabel the timeout as a bad algorithm.
    """
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_SRP_EXECUTOR, compute, *args),
            _SRP_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        code = "twofa_password_compute_timeout"
        raise TwoFactorGatewayError(code) from exc
    except (AttributeError, ValueError) as exc:
        raise TwoFactorGatewayError(_ALGO_UNSUPPORTED) from exc
