"""The container's battery telemetry.

Pure functions that calculate realistic discharging battery levels, temperatures,
and dumpsys commands to apply them to an Android container.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import NamedTuple

# BatteryManager.BATTERY_STATUS_*.
_CHARGING = 2
_DISCHARGING = 3
_FULL = 5


@dataclass(frozen=True, slots=True)
class BatteryTelemetryState:
    """The container's battery telemetry state."""

    level: int
    temp_tenths_c: int
    voltage_mv: int
    capacity_mah: int = 4500
    status: int = _DISCHARGING
    # On its charger (AC): the night between the evening's plug-in and the morning.
    plugged: bool = False

    @property
    def charge_counter_uah(self) -> int:
        """Estimated charge counter in microampere-hours."""
        return int(self.capacity_mah * 1000 * (self.level / 100.0))


# The phone's day: unplugged full-ish in its morning, drained at a steady rate
# with the screen mostly off, never below the level a user would let it reach;
# plugged in again in its evening and charged through the night to the next
# morning's level, which it always reaches before it is unplugged.
_UNPLUGGED_HOURS = (6.0, 9.0)
_UNPLUGGED_LEVEL = (88, 100)
_DRAIN_PER_HOUR = (3.0, 5.0)
_LOWEST_LEVEL = 15
_PLUGGED_HOURS = (21.5, 23.5)
_CHARGE_PER_HOUR = (15.0, 25.0)


class _Day(NamedTuple):
    """One date's draws: unplugged at ``unplugged`` o'clock at ``full``, plugged at ``plugged``."""

    unplugged: float
    full: int
    rate: float
    plugged: float
    charge: float

    def drained(self, hour: float) -> int:
        return max(_LOWEST_LEVEL, round(self.full - self.rate * (hour - self.unplugged)))

    def charged(self, hours_plugged: float, target: int) -> int:
        """The level ``hours_plugged`` after this evening's plug-in, up to ``target``."""
        return min(target, round(self.drained(self.plugged) + self.charge * hours_plugged))


def _draw_day(
    account_id: str | None, day: date, seed: int | str | None
) -> tuple[_Day, random.Random]:
    """``day``'s draws for the account, and the generator, for what else that day draws."""
    draw_seed = seed if seed is not None else f"battery:{account_id or 'telebuba'}:{day}"
    rng = random.Random(draw_seed)  # noqa: S311  # nosec B311 - fixture telemetry, not crypto
    drawn = _Day(
        unplugged=rng.uniform(*_UNPLUGGED_HOURS),
        full=rng.randint(*_UNPLUGGED_LEVEL),
        rate=rng.uniform(*_DRAIN_PER_HOUR),
        plugged=rng.uniform(*_PLUGGED_HOURS),
        charge=rng.uniform(*_CHARGE_PER_HOUR),
    )
    return drawn, rng


def generate_initial_battery_state(
    account_id: str | None = None,
    now: datetime | None = None,
    *,
    seed: int | str | None = None,
) -> BatteryTelemetryState:
    """The battery at ``now`` (temp 31.0..33.8°C), the same for that moment.

    ``now`` is the phone's own clock: its hour and date are read as they are (UTC when
    not given).

    The level follows the account's day (drawn per account and date): full when it was
    unplugged in the morning, lower each hour after, then on its charger from the
    evening, rising until the next morning's unplug. A pod is stopped and booted again
    many times a day, and each boot (and each visit to a running pod) applies this, so
    the level moves through the day and across midnight instead of standing still or
    jumping.
    """
    moment = now or datetime.now(UTC)
    hour = moment.hour + moment.minute / 60
    day, rng = _draw_day(account_id, moment.date(), seed)
    plugged = True
    if hour < day.unplugged:  # still on last night's charger
        evening, _ = _draw_day(account_id, moment.date() - timedelta(days=1), seed)
        level = evening.charged(hour + 24 - evening.plugged, day.full)
    elif hour < day.plugged:
        level, plugged = day.drained(hour), False
    else:
        morning, _ = _draw_day(account_id, moment.date() + timedelta(days=1), seed)
        level = day.charged(hour - day.plugged, morning.full)
    status = _DISCHARGING
    if plugged:
        status = _FULL if level >= 100 else _CHARGING  # noqa: PLR2004 - a percentage
    temp_tenths_c = rng.randint(310, 338)  # 31.0°C .. 33.8°C
    voltage_mv = 3650 + int((level / 100.0) * 620) + rng.randint(-15, 15)
    return BatteryTelemetryState(
        level=level,
        temp_tenths_c=temp_tenths_c,
        voltage_mv=voltage_mv,
        capacity_mah=4500,
        status=status,
        plugged=plugged,
    )


def build_battery_telemetry_commands(state: BatteryTelemetryState) -> list[str]:
    """Build dumpsys battery commands to apply the state.

    redroid has no battery and reports none until told ``present``, which comes last:
    a present battery still at level 0 shuts Android down at once. Its voltage and
    technology cannot be set (``dumpsys battery set`` refuses both).
    """
    return [
        f"dumpsys battery set ac {int(state.plugged)}",
        "dumpsys battery set usb 0",
        "dumpsys battery set wireless 0",
        f"dumpsys battery set status {state.status}",
        f"dumpsys battery set level {state.level}",
        f"dumpsys battery set temp {state.temp_tenths_c}",
        f"dumpsys battery set counter {state.charge_counter_uah}",
        "dumpsys battery set present 1",
    ]
