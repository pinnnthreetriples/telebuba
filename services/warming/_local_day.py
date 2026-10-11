"""The warming day in the account's own time zone.

The daily budget, the quiet day and the wait for a new day all follow the
account's local calendar, not UTC: a UTC day ends in the US evening, and a fleet
woken at one UTC midnight wakes on the same second everywhere. An unknown or
unresolvable time zone falls back to UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.config import settings
from services.warming.pacing import _morning_offset_seconds

if TYPE_CHECKING:
    from random import Random


def _local_zone(tz_name: str | None) -> tzinfo:
    """The account's zone, or UTC when it is unknown or not a valid IANA name."""
    if tz_name is None:
        return UTC
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def _local_date(tz_name: str | None, now: datetime) -> str:
    """The account's local calendar date at ``now``, as ``YYYY-MM-DD``."""
    return now.astimezone(_local_zone(tz_name)).date().isoformat()


def _next_local_wake(tz_name: str | None, now: datetime, rng: Random, account_id: str) -> datetime:
    """When an account waiting for a new day wakes: tomorrow's local morning, in UTC.

    The wake is the start of the active window on the account's next local day,
    plus its stable morning chronotype and daily wobble, so a parked fleet wakes
    spread across each account's own morning rather than at one UTC instant.
    """
    zone = _local_zone(tz_name)
    tomorrow = now.astimezone(zone).date() + timedelta(days=1)
    morning = datetime.combine(tomorrow, time(settings.warming.active_hours_start), tzinfo=zone)
    wake = morning + timedelta(seconds=_morning_offset_seconds(account_id, rng))
    return wake.astimezone(UTC)
