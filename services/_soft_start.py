"""The soft neurocomment start: which day of it an account is on, and that day's caps.

The operator picks the start mode at the hand-off (``nc_start_mode`` / ``nc_handoff_at``
on the warming row). A ``soft`` start runs ``settings.soft_start.days`` days counted in
whole 24-hour periods from the hand-off; ``services._account_limits`` lowers the fleet
caps to the numbers below for those days. Pure functions — every caller already holds
the warming row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, NamedTuple

from core.config import settings

if TYPE_CHECKING:
    from schemas.warming import WarmingStateRecord

_SECONDS_PER_DAY = 86_400


class SoftCaps(NamedTuple):
    """One soft day's ceilings, the lower of which and the fleet's the gates enforce."""

    comments_per_hour: int
    comments_per_channel_per_day: int
    joins_per_day: int
    # Rolling-24h comments per account — a cap only the soft start has.
    comments_per_day: int


def soft_day(record: WarmingStateRecord | None, now: datetime) -> int | None:
    """The 1-based day of a running soft start, ``None`` outside one."""
    if record is None or not record.nc_handed_off or record.nc_start_mode != "soft":
        return None
    if record.nc_handoff_at is None:
        return None
    try:
        handed = datetime.fromisoformat(record.nc_handoff_at)
    except ValueError:
        return None
    handed = handed if handed.tzinfo is not None else handed.replace(tzinfo=UTC)
    day = int((now - handed).total_seconds() // _SECONDS_PER_DAY) + 1
    return day if 1 <= day <= settings.soft_start.days else None


def soft_caps(day: int) -> SoftCaps:
    """The caps of soft day ``day``: early or late step, and the per-day comment ramp."""
    soft = settings.soft_start
    span = soft.days - 1
    ramp = soft.last_day_comments - soft.first_day_comments
    # Half-up, so a 5 → 15 ramp over five days reads 5, 8, 10, 13, 15.
    per_day = soft.first_day_comments + (int(ramp * (day - 1) / span + 0.5) if span else 0)
    if day <= soft.early_days:
        return SoftCaps(
            soft.early_comments_per_hour,
            soft.early_comments_per_channel_per_day,
            soft.early_joins_per_day,
            per_day,
        )
    return SoftCaps(
        soft.late_comments_per_hour,
        soft.late_comments_per_channel_per_day,
        soft.late_joins_per_day,
        per_day,
    )
