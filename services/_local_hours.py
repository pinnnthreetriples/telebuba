"""Pure helpers for "the account's local clock": a zone by name and an hour window.

Shared by features that keep to an account's waking hours. Nothing here reads the clock, the
database or settings: callers pass the instant and the hours. ``services.warming`` keeps its own
older copies of the same two rules (``pacing._in_quiet_hours``, ``_local_day._local_zone``); this
module is the one new code uses, so a later change can point warming here without changing it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def known_zone(tz_name: str | None) -> ZoneInfo | None:
    """The IANA zone, or ``None`` when the name is missing or not a zone (never a guess)."""
    if not tz_name:
        return None
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def in_hours(local: datetime, start_hour: int, end_hour: int) -> bool:
    """Whether the hour of ``local`` falls in ``[start, end)``; wraps midnight when start > end.

    ``start == end`` is no window at all (always ``False``).
    """
    if start_hour == end_hour:
        return False
    if start_hour < end_hour:
        return start_hour <= local.hour < end_hour
    return local.hour >= start_hour or local.hour < end_hour


def next_hour_boundary(local: datetime, hour: int) -> datetime:
    """The first local ``hour``:00 strictly after ``local`` (wall-clock arithmetic, DST-safe)."""
    boundary = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if boundary <= local:
        boundary += timedelta(days=1)
    return boundary


def last_hour_boundary(local: datetime, hour: int) -> datetime:
    """The latest local ``hour``:00 at or before ``local``."""
    boundary = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if boundary > local:
        boundary -= timedelta(days=1)
    return boundary
