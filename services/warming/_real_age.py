"""Real account age — the operator's Telegram registration date (Прогрев 2.0, item 4.8).

Warming counts an account's age from when it joined the app (``created_at``), which
for a bought account says nothing about how long Telegram has known it. The operator
may give the registration date at upload; an account older than half a year then gets
its DMs a day earlier, and one older than a year starts «warming» on day 2. The
first-day floor (``phase_hard_floor_age_hours``) and the trust ceiling still apply:
this only moves the age the phase table reads, never past them.

A leaf like ``_phases`` (config + stdlib only) so the board and the engine share it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.config import settings

if TYPE_CHECKING:
    from schemas.accounts import AccountRead

_HOURS_PER_DAY = 24.0
# Past the ``settling`` bound by this much: the first hour of ``warming``.
_INTO_PHASE_HOURS = 1.0


def registered_days(registered_at: str | None, now: datetime) -> float | None:
    """Days since the Telegram registration date; ``None`` when unknown or unreadable."""
    if not registered_at:
        return None
    try:
        registered = datetime.fromisoformat(registered_at)
    except ValueError:
        return None
    if registered.tzinfo is None:
        registered = registered.replace(tzinfo=UTC)
    return max(0.0, (now - registered).total_seconds() / 3600.0 / _HOURS_PER_DAY)


def shifted_age_hours(age_hours: float, account: AccountRead | None, now: datetime) -> float:
    """The age the phase table reads: a year-old account is in «warming» from day 2.

    Day one is untouched (``phase_hard_floor_age_hours``), and an account already past
    the shift keeps its own, larger age.
    """
    warm = settings.warming
    days = registered_days(account.telegram_registered_at if account else None, now)
    settling_bound = warm.phase_day_bound.get("settling")
    if (
        days is None
        or settling_bound is None
        or days < warm.registered_fast_phase_min_days
        or age_hours < warm.phase_hard_floor_age_hours
    ):
        return age_hours
    return max(age_hours, settling_bound * _HOURS_PER_DAY + _INTO_PHASE_HOURS)


def dm_open_after_hours(registered_at: str | None, now: datetime) -> float:
    """Warming hours before DMs open: a day less for an account older than half a year."""
    warm = settings.warming
    days = registered_days(registered_at, now)
    if days is not None and days >= warm.registered_dm_early_min_days:
        return max(0.0, warm.dm_min_age_hours - warm.registered_dm_early_hours)
    return warm.dm_min_age_hours
