"""Per-account, per-day warming totals: what one settled cycle adds, and the read back.

``stats_day`` is the single place that decides which calendar day a cycle belongs
to, so switching from the UTC date to the account's local date is a one-function
change.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.logging import log_event
from core.repositories.warming_daily_stats import (
    add_warming_daily_stats,
    list_warming_daily_stats,
)
from schemas.warming_daily_stats import WarmingDailyStats, WarmingDailyStatsList
from services.trust import account_trust_score

if TYPE_CHECKING:
    from schemas.warming import WarmingCycleResult, WarmingPhase

# Stdlib sink for the traceback; the persisted event carries only the error type.
logger = logging.getLogger(__name__)

_FLOOD_STATUSES = frozenset({"flood_wait", "peer_flood"})


def stats_day(now: datetime) -> str:
    """The ``YYYY-MM-DD`` day a moment is counted under — the UTC date for now."""
    return now.astimezone(UTC).date().isoformat()


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
    retired is never counted.
    """
    account_id = result.account_id
    try:
        trust = await account_trust_score(account_id)
        day = stats_day(now or datetime.now(UTC))
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
    since = stats_day(moment - timedelta(days=max(days, 1) - 1))
    return WarmingDailyStatsList(items=await list_warming_daily_stats(account_id, since))
