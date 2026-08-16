"""In-memory state: the LLM budget, generation single-flight, and the run fences.

Shape copied from :mod:`services.neurocomment._discovery_state` — one module of
synchronous functions, nothing persisted, and the claim taken inside an await-free
section so it is atomic under a single event loop without a lock.

Nothing here is durable on purpose. The budget guards spend within a running
process; a restart forgives it, which is the same bargain the discovery search
counter already makes. A table would need a migration and a sweeper for state
that does not outlive the process.

**Why a budget exists at all.** The project keeps no token accounting anywhere, and
one campaign is ten accounts across twenty targets: a mistyped topic that keeps
being regenerated is a four-figure bill with nothing between it and the card. The
cap is counted in CALLS rather than tokens because calls are what this process can
actually see — and it counts every HTTP request, the gateway's own transient
retries included, since a retry costs exactly what the first try did.
"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingRefusalCode

_LLM_WINDOW = timedelta(hours=24)

# Rolling-24h timestamps of provider calls, fleet-wide rather than per campaign:
# the bill is one bill, and ten campaigns each under their own share of it is the
# failure this is here to prevent.
_LLM_CALLS: deque[datetime] = deque()
# Campaigns with a generation in flight. A second click would otherwise spend the
# budget twice and race two writes over the same rows.
_GENERATING: set[str] = set()
# Campaigns with a START in flight. The status column cannot answer this on its own:
# ``running`` is written several awaits after the check that reads it, and both halves
# of a double click straddle that gap.
_STARTING: set[str] = set()

# campaign_id -> the newest run generation. Bumped by BOTH Start and Stop, and every
# external call of a run checks it before and after itself, so a coroutine parked in a
# step delay when Stop was pressed wakes up fenced. A ``status='stopping'`` flip cannot
# do this: it is a row, and nothing reads a row on the way out of a sleep.
_RUN_GENERATIONS: dict[str, int] = {}
# campaign_id -> the run_id currently entitled to write this campaign's terminal row.
# A SECOND map and not a re-use of the counter above, because the two answer different
# questions: Stop bumps the generation (so the old run stops acting) but the run it
# stopped is still the one that must settle. Only a NEWER run displaces that right,
# which is what stops a late finisher writing ``done`` over its successor's ``running``.
_RUN_OWNER: dict[str, str] = {}


def _prune(now: datetime) -> None:
    cutoff = now - _LLM_WINDOW
    while _LLM_CALLS and _LLM_CALLS[0] < cutoff:
        _LLM_CALLS.popleft()


def at_daily_llm_cap(now: datetime | None = None) -> bool:
    """Has the fleet used up its rolling-24h generation allowance?

    A configured ``0`` reads as "never generate", not as "no limit" — the same way
    neurocomment's search cap reads it, and the only reading that makes a zero
    budget mean anything.
    """
    moment = now or datetime.now(UTC)
    _prune(moment)
    return len(_LLM_CALLS) >= settings.neuroshilling.max_llm_calls_per_day


def record_llm_call(now: datetime | None = None, *, calls: int = 1) -> None:
    """Charge ``calls`` provider calls to the window, all at the same moment.

    Counted in HTTP requests rather than in attempts: the gateway retries a
    transient failure inside one call, so an attempt costs ``max_retries + 1``
    requests and the caller charges that.
    """
    _LLM_CALLS.extend([now or datetime.now(UTC)] * calls)


def try_start_generation(
    campaign_id: str,
    now: datetime | None = None,
) -> NeuroshillingRefusalCode | None:
    """Claim this campaign's generation slot, or say why not.

    Contains no ``await`` by design: everything from the check to the claim is one
    synchronous section, so a second request cannot straddle it. The caller must
    release with :func:`finish_generation` in a ``finally``.

    The cap test here is the door, not a reservation — nothing is charged until a
    call is actually made, so two campaigns clicked together at cap-1 both get in.
    The generation loop re-reads the cap on every pass, which is what stops them.
    """
    if campaign_id in _GENERATING:
        return "generation_in_progress"
    if at_daily_llm_cap(now):
        return "llm_daily_limit_reached"
    _GENERATING.add(campaign_id)
    return None


def finish_generation(campaign_id: str) -> None:
    _GENERATING.discard(campaign_id)


def try_claim_start(campaign_id: str) -> bool:
    """Claim this campaign's start slot; ``False`` means a start is already in flight.

    Same shape as :func:`try_start_generation` and for the same reason: the caller's
    "is this campaign already live?" test reads a column that ``start_campaign`` only
    writes several awaits later — the roster reads and the account claim sit in between
    — so two requests both pass that test. Taking this claim in the SAME synchronous
    section as the test is what makes the pair atomic. The caller must release with
    :func:`finish_start` in a ``finally``.
    """
    if campaign_id in _STARTING:
        return False
    _STARTING.add(campaign_id)
    return True


def finish_start(campaign_id: str) -> None:
    _STARTING.discard(campaign_id)


def start_in_flight(campaign_id: str) -> bool:
    """Is a start of this campaign between its claim and its spawn right now?

    Read by the run task's done callback, which hands a stopped run's roster back: a
    start that has already claimed those accounts but not yet published its task would
    otherwise have them taken from under it.
    """
    return campaign_id in _STARTING


def begin_run(campaign_id: str, run_id: str) -> int:
    """Publish a new run generation for ``campaign_id`` and return it.

    The counter only ever rises, and it is never removed on settle: a reused value
    would let a coroutine from two runs ago pass the fence.
    """
    generation = _RUN_GENERATIONS[campaign_id] = _RUN_GENERATIONS.get(campaign_id, 0) + 1
    _RUN_OWNER[campaign_id] = run_id
    return generation


def revoke_run(campaign_id: str) -> None:
    """Fence every coroutine of the current run without naming a successor.

    Stop and shutdown both call this. The settlement right is deliberately left where
    it was: the run being stopped is still the one that owns its terminal row.
    """
    _RUN_GENERATIONS[campaign_id] = _RUN_GENERATIONS.get(campaign_id, 0) + 1


def run_is_current(campaign_id: str, generation: int) -> bool:
    """Is ``generation`` still the live run of this campaign?

    The predicate ``_seams.run_scope`` is handed, checked before and after every
    external call.
    """
    return _RUN_GENERATIONS.get(campaign_id, 0) == generation


def claim_settlement(campaign_id: str, run_id: str) -> bool:
    """Take the right to write this campaign's terminal row.

    Refused in exactly one case: a DIFFERENT run owns the campaign. That is the late
    finisher trying to write ``done`` over its successor's ``running``.

    No entry at all is granted, not refused, and that is what unwedges the Stop race:
    Stop reads a live campaign, the run task settles in the gap before Stop writes
    ``stopping``, and that write resurrects a terminal row. The task took the entry
    away when it settled, so Stop's fallback finds nothing — and it is precisely the
    run whose settlement is being repeated, so repeating it (the same terminal row,
    the same release) is what the campaign needs rather than a refusal that would
    leave it ``stopping`` until a restart.

    Contains no ``await``, so the check and the take cannot be straddled.
    """
    owner = _RUN_OWNER.get(campaign_id)
    if owner is not None and owner != run_id:
        return False
    _RUN_OWNER.pop(campaign_id, None)
    return True


def reset_for_tests() -> None:
    _LLM_CALLS.clear()
    _GENERATING.clear()
    _STARTING.clear()
    _RUN_GENERATIONS.clear()
    _RUN_OWNER.clear()
