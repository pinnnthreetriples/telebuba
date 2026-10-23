"""The container's battery telemetry.

Pure functions that calculate realistic discharging battery levels, temperatures,
and dumpsys commands to apply them to an Android container.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class BatteryTelemetryState:
    """The container's battery telemetry state."""

    level: int
    temp_tenths_c: int
    voltage_mv: int
    capacity_mah: int = 4500
    status: int = 3  # BatteryManager.BATTERY_STATUS_DISCHARGING = 3

    @property
    def charge_counter_uah(self) -> int:
        """Estimated charge counter in microampere-hours."""
        return int(self.capacity_mah * 1000 * (self.level / 100.0))


# The phone's day: unplugged full-ish in the morning (UTC), drained at a steady rate
# with the screen mostly off, never below the level a user would let it reach.
_UNPLUGGED_HOURS = (6.0, 9.0)
_UNPLUGGED_LEVEL = (88, 100)
_DRAIN_PER_HOUR = (3.0, 5.0)
_LOWEST_LEVEL = 15


def generate_initial_battery_state(
    account_id: str | None = None,
    now: datetime | None = None,
    *,
    seed: int | str | None = None,
) -> BatteryTelemetryState:
    """The discharging battery at ``now`` (temp 31.0..33.8°C), the same for that moment.

    The level follows the account's day (drawn per account and date): full when it was
    unplugged in the morning, lower each hour after, charged again by the next morning.
    A pod is stopped and booted again many times a day, and each boot (and each visit to
    a running pod) applies this, so the level drops through the day instead of standing
    still or jumping back up.
    """
    moment = now or datetime.now(UTC)
    if seed is not None:
        draw_seed: int | str = seed
    else:
        draw_seed = f"battery:{account_id or 'telebuba'}:{moment.date().isoformat()}"
    rng = random.Random(draw_seed)  # noqa: S311  # nosec B311 - fixture telemetry, not crypto
    unplugged = rng.uniform(*_UNPLUGGED_HOURS)
    full = rng.randint(*_UNPLUGGED_LEVEL)
    rate = rng.uniform(*_DRAIN_PER_HOUR)
    temp_tenths_c = rng.randint(310, 338)  # 31.0°C .. 33.8°C
    hours = moment.hour + moment.minute / 60 - unplugged
    level = max(_LOWEST_LEVEL, round(full - rate * max(0.0, hours)))
    voltage_mv = 3650 + int((level / 100.0) * 620) + rng.randint(-15, 15)
    return BatteryTelemetryState(
        level=level,
        temp_tenths_c=temp_tenths_c,
        voltage_mv=voltage_mv,
        capacity_mah=4500,
        status=3,
    )


def step_battery_state(
    state: BatteryTelemetryState,
    seed: int | str | None = None,
    *,
    min_level: int = 15,
) -> BatteryTelemetryState:
    """Step battery state down by 1% with temperature jitter."""
    rng = random.Random(seed)  # noqa: S311  # nosec B311 - fixture telemetry, not crypto
    next_level = max(min_level, state.level - 1)
    temp_delta = rng.choice((-3, -2, -1, 0, 1, 2, 3))
    next_temp = max(295, min(368, state.temp_tenths_c + temp_delta))
    next_voltage = 3650 + int((next_level / 100.0) * 620) + rng.randint(-10, 10)
    return BatteryTelemetryState(
        level=next_level,
        temp_tenths_c=next_temp,
        voltage_mv=next_voltage,
        capacity_mah=state.capacity_mah,
        status=state.status,
    )


def build_battery_telemetry_commands(state: BatteryTelemetryState) -> list[str]:
    """Build dumpsys battery commands to apply the state."""
    return [
        "dumpsys battery set ac 0",
        "dumpsys battery set usb 0",
        "dumpsys battery set wireless 0",
        f"dumpsys battery set status {state.status}",
        f"dumpsys battery set level {state.level}",
        f"dumpsys battery set temp {state.temp_tenths_c}",
        f"dumpsys battery set counter {state.charge_counter_uah}",
    ]
