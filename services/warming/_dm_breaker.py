"""Fleet circuit breaker for inter-account DMs (Прогрев 2.0, 1.5).

On 16-17.09 a second account wrote a DM after the first one was spam-blocked and got
blocked too. So ``dm_breaker_floods`` DM spam blocks anywhere in the fleet within
``dm_breaker_window_hours`` pause DMs for EVERY account for ``dm_breaker_pause_hours``.

The pause lives in ``warming_settings.dm_paused_until``. The stamp doubles as the
"already spent" marker: blocks recorded before it never count again, so neither an
expired pause nor one the operator lifted re-trips on the blocks that caused it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from core.config import settings
from core.logging import log_event
from core.repositories.warming import load_warming_settings
from core.repositories.warming_dm_pause import store_dm_paused_until
from core.repositories.warming_floods import count_warming_flood_events_since
from services.warming._phases import _parse_utc

logger = logging.getLogger(__name__)

# The ``warming_flood_events.action`` a DM spam block is recorded with (``_chat``).
DM_ACTION = "send_dm"


def active_dm_pause(stamp: str | None, now: datetime) -> datetime | None:
    """The instant fleet DMs reopen, or ``None`` when no pause is in force at ``now``."""
    until = _parse_utc(stamp)
    return until if until is not None and until > now else None


async def fleet_dm_paused_now() -> bool:
    """A fresh read of the pause (the cache is dropped on every write to it)."""
    stamp = (await load_warming_settings()).dm_paused_until
    return active_dm_pause(stamp, datetime.now(UTC)) is not None


async def trip_dm_breaker(now: datetime) -> datetime | None:
    """Pause fleet DMs when the recent DM spam blocks reach the threshold.

    Returns the new pause end, or ``None`` when nothing tripped (already paused, or
    too few blocks since the window start / the last pause).
    """
    warm = settings.warming
    stamp = _parse_utc((await load_warming_settings()).dm_paused_until)
    if stamp is not None and stamp > now:
        return None
    since = now - timedelta(hours=warm.dm_breaker_window_hours)
    if stamp is not None:
        since = max(since, stamp)
    floods = await count_warming_flood_events_since(DM_ACTION, since.isoformat())
    if floods < warm.dm_breaker_floods:
        return None
    until = now + timedelta(hours=warm.dm_breaker_pause_hours)
    await store_dm_paused_until(until.isoformat())
    await log_event(
        "WARNING",
        "warming_dm_fleet_paused",
        extra={"until": until.isoformat(), "floods": floods},
    )
    return until


async def note_dm_flood(action: str | None) -> None:
    """Run the breaker after a DM spam block; a failure is logged, never raised.

    The caller is on its way to the peer-flood → quarantine transition, which matters
    more than the fleet pause. Cancellation is not an ``Exception`` and propagates.
    """
    if action != DM_ACTION:
        return
    try:
        await trip_dm_breaker(datetime.now(UTC))
    except Exception as exc:  # noqa: BLE001 - the breaker must not block the safety transition.
        logger.warning("warming DM breaker check failed: %s", type(exc).__name__, exc_info=exc)
