"""The operator-chosen start window: when an account's first warming cycle lands.

The start dialog picks a delay of 1-24 hours. A single start lands at the end of it;
a pool start draws each account its own moment inside it, so a fleet does not wake
in one second. Either way the moment goes through the same night rule as every
other schedule (``_shift_to_active_hours``): a night-time first cycle moves to the
account's local morning. The cold-start ceiling does not apply — the operator chose
how long the account rests. Without a delay the loop keeps its cold-start spread.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from random import Random
from typing import TYPE_CHECKING

from core.config import settings
from core.db import list_accounts_by_ids
from core.phone_geo import timezone_for_phone
from schemas._warming_start import StartPreview, StartPreviewItem
from services.warming import _seams
from services.warming._local_day import _local_zone
from services.warming.pacing import _SECONDS_PER_HOUR, _in_quiet_hours, _shift_to_active_hours

if TYPE_CHECKING:
    from schemas._warming_start import StartPreviewRequest
    from schemas.accounts import AccountRead
    from schemas.warming import StartWarmingRequest


class _CentredRandom(Random):
    """Draws the daily wobble at its centre, so a preview shows the stable chronotype."""

    def triangular(self, low: float = 0.0, high: float = 1.0, mode: float | None = None) -> float:
        return (low + high) / 2 if mode is None else mode


def _is_night(moment: datetime, tz_name: str | None) -> bool:
    """True when ``moment`` falls outside the account's active local window."""
    warm = settings.warming
    if not warm.active_hours_enabled or warm.active_hours_start == warm.active_hours_end:
        return False
    local = moment.astimezone(_local_zone(tz_name))
    return not _in_quiet_hours(local, warm.active_hours_start, warm.active_hours_end)


def _window_touches_night(
    start: datetime, hours: int, tz_name: str | None, *, spread: bool
) -> bool:
    """Whether any first-cycle moment the window allows would move to the morning.

    The night rule works in whole local hours, so one probe per hour plus the end
    meets every hour the window crosses.
    """
    if not spread:
        return _is_night(start + timedelta(hours=hours), tz_name)
    probes = [start + timedelta(hours=step) for step in range(hours + 1)]
    return any(_is_night(probe, tz_name) for probe in probes)


def first_run_at(account: AccountRead, data: StartWarmingRequest) -> str | None:
    """The persisted first-cycle time for an operator-chosen delay, or ``None``.

    ``None`` leaves ``next_run_at`` empty, so the loop draws its cold-start spread.
    """
    if data.start_delay_hours is None:
        return None
    window = data.start_delay_hours * _SECONDS_PER_HOUR
    offset = _seams.rng.uniform(0.0, window) if data.spread_start else window
    candidate = datetime.now(UTC) + timedelta(seconds=offset)
    tz_name = timezone_for_phone(account.phone)
    return _shift_to_active_hours(candidate, tz_name, _seams.rng, account.account_id).isoformat()


async def preview_start(data: StartPreviewRequest) -> StartPreview:
    """When each account's first cycle lands, in its own zone, for the start dialog.

    Unknown ids are previewed in UTC like an account without a phone zone: the
    preview is advice, and the start itself refuses an unknown account.
    """
    now = datetime.now(UTC)
    phones = {
        a.account_id: a.phone for a in (await list_accounts_by_ids(data.account_ids)).accounts
    }
    rng = _CentredRandom()
    hours = data.start_delay_hours
    first = now if data.spread_start else now + timedelta(hours=hours)
    last = now + timedelta(hours=hours)
    items = []
    for account_id in data.account_ids:
        tz_name = timezone_for_phone(phones.get(account_id))
        items.append(
            StartPreviewItem(
                account_id=account_id,
                time_zone=str(_local_zone(tz_name)),
                earliest_at=_shift_to_active_hours(first, tz_name, rng, account_id).isoformat(),
                latest_at=_shift_to_active_hours(last, tz_name, rng, account_id).isoformat(),
                shifted=_window_touches_night(now, hours, tz_name, spread=data.spread_start),
            )
        )
    return StartPreview(items=items)
