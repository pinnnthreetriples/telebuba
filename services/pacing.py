"""Per-key send pacing and the shared human-pause draw.

Two things live here, both pure of any domain.

:func:`await_send_slot` is the project's first per-account tempo gate. Nothing
else in the codebase is one: warming's ``pacing`` module is a pure schedule
calculator with no state between calls, neurocomment's cooldowns are "blocked
until T" deadlines handed down by Telegram's own flood signal, and a lease is
ownership rather than tempo. The algorithm is :func:`core.gemini._await_slot`
generalised to a dictionary of keys.

**Do not use ``services.warming.account_lock`` for this.** That lock is the
account LIFECYCLE mutex — Start, Stop, Promote, Handoff and remove_account all
take it — so holding it across a pacing sleep would block the operator's buttons
for that account for tens of seconds. The pacer keeps its own private map and
knows nothing about lifecycle. For the same reason the nesting order is fixed:
``await_send_slot`` is awaited OUTSIDE ``account_lock``, never inside it.

:func:`human_delay` is the clipped log-normal draw warming has always used,
lifted here so it exists once instead of once per feature. Everything it depends
on is injected, so it stays a pure function and each caller keeps its own seam.

Honest limit: the marginal distribution of a delay is the least important part of
looking human. Real conversation is bursty within sessions and bounded by a sleep
schedule; a log-normal sampled round the clock is chosen here because it already
existed and beats a uniform draw, not because it is demonstrably human-like.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import random

# One lock and one clock per key. Not persisted: a restart forgets at most one
# unspaced send, and the same argument ``services.neurocomment._state`` makes for
# its cooldowns applies — this is a single-process gate, not shared state.
_LOCKS: dict[str, asyncio.Lock] = {}
_LAST: dict[str, float] = {}


async def await_send_slot(key: str, min_gap_seconds: float) -> None:
    """Sleep until ``min_gap_seconds`` have passed since the last slot for ``key``.

    ``min_gap_seconds <= 0`` disables the gate and never touches the clock, so a
    caller that opts out does not perturb the spacing of one that opted in.

    The jitter is the CALLER's: pass a freshly drawn :func:`human_delay` and the
    fixed-interval gate becomes a scattered pause without a line of extra code.
    Keys are the caller's too — ``account_id`` for sends and ``f"join:{id}"`` for
    joins keeps the two tempos independent while still serialising each of them
    per account, however many targets are in flight.
    """
    if min_gap_seconds <= 0:
        return
    lock = _LOCKS.get(key)
    if lock is None:
        lock = _LOCKS[key] = asyncio.Lock()
    async with lock:
        wait = _LAST.get(key, 0.0) + min_gap_seconds - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _LAST[key] = time.monotonic()


def human_delay(
    min_seconds: float,
    max_seconds: float,
    *,
    rng: random.Random,
    mu: float,
    sigma: float,
) -> float:
    """A human-like pause in ``[min, max]`` drawn from a clipped log-normal.

    Real users are bursty: many short gaps with a heavy tail of long ones. The
    log-normal fraction (median below the midpoint, occasional spike to the max)
    is mapped onto the configured range — unlike a uniform draw, which is the
    most obvious bot signature there is.

    ``rng`` is injected rather than module-global so each domain keeps patching
    its own seam and the arithmetic stays identical across all of them.
    """
    lo, hi = sorted((min_seconds, max_seconds))
    if hi <= lo:
        return lo
    fraction = min(1.0, rng.lognormvariate(mu, sigma))
    # min(hi, ...) guards the float-rounding edge where fraction == 1.0 makes
    # lo + (hi - lo) overshoot hi by an ULP — the result must stay within [lo, hi].
    return min(hi, lo + fraction * (hi - lo))


def reset_for_tests() -> None:
    _LOCKS.clear()
    _LAST.clear()
