"""Per-account, per-day warming totals: what one settled cycle adds, and the read back.

``stats_day`` is the single place that decides which calendar day a cycle belongs
to: the account's local date, resolved exactly like the daily budget's day.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from core.logging import log_event
from core.repositories.warming_daily_stats import (
    add_warming_daily_stats,
    list_warming_daily_stats,
)
from schemas.warming_daily_stats import WarmingDailyStats, WarmingDailyStatsList
from services.trust import account_trust_score
from services.warming._local_day import _local_date
from services.warming.pacing import _account_tz

if TYPE_CHECKING:
    from schemas.warming import WarmingCycleResult, WarmingPhase

# Stdlib sink for the traceback; the persisted event carries only the error type.
logger = logging.getLogger(__name__)

_FLOOD_STATUSES = frozenset({"flood_wait", "peer_flood"})
# Strong references to stats writes that outlived a cancelled caller (see below);
# the event loop alone holds tasks weakly.
_DETACHED: set[asyncio.Task[None]] = set()


async def stats_day(account_id: str, now: datetime) -> str:
    """The ``YYYY-MM-DD`` day a moment is counted under: the account's local date.

    Same zone resolution as the daily budget, so a day's totals and its budget agree;
    an unknown zone falls back to UTC.
    """
    return _local_date(await _account_tz(account_id), now)


def cycle_increment(
    result: WarmingCycleResult,
    *,
    day: str,
    phase: WarmingPhase | None,
) -> WarmingDailyStats:
    """What one settled cycle adds to its day; trust is filled in by the caller."""
    return WarmingDailyStats(
        account_id=result.account_id,
        day=day,
        # A skipped cycle (no channels configured) did no warming work — the same
        # rule that keeps it off ``cycles_completed``.
        cycles=0 if result.status == "skipped" else 1,
        reads=result.channels_read,
        reactions=result.reactions_sent,
        joins=result.channels_joined,
        dms=result.messages_sent,
        failures=result.failures,
        floods=1 if result.status in _FLOOD_STATUSES else 0,
        phase=phase,
    )


async def record_cycle_stats(
    result: WarmingCycleResult,
    phase: WarmingPhase | None,
    now: datetime | None = None,
) -> None:
    """Add a settled cycle to its day's row. Best-effort: never fails the cycle.

    Call only after the cycle's state write landed, so a cycle a newer generation
    retired is never counted. The write is shielded: a Stop that cancels the loop
    right after that state write still lets the increment finish, so the state
    never counts a cycle its day's row is missing. The caller's cancellation still
    propagates at once.
    """
    task = asyncio.ensure_future(_record_cycle_stats(result, phase, now or datetime.now(UTC)))
    _DETACHED.add(task)
    task.add_done_callback(_DETACHED.discard)
    await asyncio.shield(task)


async def _record_cycle_stats(
    result: WarmingCycleResult,
    phase: WarmingPhase | None,
    now: datetime,
) -> None:
    account_id = result.account_id
    try:
        trust = await account_trust_score(account_id)
        day = await stats_day(account_id, now)
        increment = cycle_increment(result, day=day, phase=phase).model_copy(
            update={"trust_score": trust.score, "trust_band": trust.band},
        )
        await add_warming_daily_stats(increment)
    except Exception as exc:  # statistics must never break a settled cycle.
        logger.exception("daily stats write failed for %s", account_id)
        await log_event(
            "WARNING",
            "warming_daily_stats_write_failed",
            account_id=account_id,
            extra={"error_type": type(exc).__name__},
        )


async def load_daily_stats(
    account_id: str,
    days: int,
    *,
    now: datetime | None = None,
) -> WarmingDailyStatsList:
    """The last ``days`` days of one account (today included), oldest first."""
    moment = now if now is not None else datetime.now(UTC)
    # Step back in calendar days from the local date, not in 24h spans from the UTC
    # moment: across a DST change those land on a different local day.
    today = date.fromisoformat(await stats_day(account_id, moment))
    since = (today - timedelta(days=max(days, 1) - 1)).isoformat()
    return WarmingDailyStatsList(items=await list_warming_daily_stats(account_id, since))
