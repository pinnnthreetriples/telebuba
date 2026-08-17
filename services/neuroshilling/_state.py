"""In-memory state for neuroshilling generation: the LLM budget and single-flight.

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


def reset_for_tests() -> None:
    _LLM_CALLS.clear()
    _GENERATING.clear()
