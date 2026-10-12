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


class _EdgeRandom(Random):
    """Draws the daily wobble at one edge, so a preview spans every real wake time."""

    def __init__(self, *, upper: bool) -> None:
        super().__init__()
        self._upper = upper

    def triangular(self, low: float = 0.0, high: float = 1.0, mode: float | None = None) -> float:
        del mode
        return high if self._upper else low


_LOWER = _EdgeRandom(upper=False)
_UPPER = _EdgeRandom(upper=True)


def _is_night(moment: datetime, tz_name: str | None) -> bool:
    """True when ``moment`` falls outside the account's active local window."""
    warm = settings.warming
    if not warm.active_hours_enabled or warm.active_hours_start == warm.active_hours_end:
        return False
    local = moment.astimezone(_local_zone(tz_name))
    return not _in_quiet_hours(local, warm.active_hours_start, warm.active_hours_end)


def _probes(start: datetime, end: datetime, tz_name: str | None) -> list[datetime]:
    """Candidates whose shifted times bound every first cycle the window allows.

    The night rule changes only on whole local hours: inside a daytime stretch a
    candidate stays put (its extremes are the stretch's ends) and a whole night maps
    to one morning. So the window's ends plus each local hour boundary, and the
    instant before it, meet every extreme.
    """
    probes = [start, end]
    boundary = start.astimezone(_local_zone(tz_name)).replace(minute=0, second=0, microsecond=0)
    boundary += timedelta(hours=1)
    while boundary < end:
        probes += [boundary - timedelta(microseconds=1), boundary]
        boundary += timedelta(hours=1)
    return [probe.astimezone(UTC) for probe in probes]


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
    hours = data.start_delay_hours
    last = now + timedelta(hours=hours)
    first = now if data.spread_start else last
    items = []
    for account_id in data.account_ids:
        tz_name = timezone_for_phone(phones.get(account_id))
        probes = _probes(first, last, tz_name)
        lows = [_shift_to_active_hours(p, tz_name, _LOWER, account_id) for p in probes]
        highs = [_shift_to_active_hours(p, tz_name, _UPPER, account_id) for p in probes]
        items.append(
            StartPreviewItem(
                account_id=account_id,
                time_zone=str(_local_zone(tz_name)),
                earliest_at=min(lows).isoformat(),
                latest_at=max(highs).isoformat(),
                shifted=any(_is_night(probe, tz_name) for probe in probes),
            )
        )
    return StartPreview(items=items)
