"""The container's battery and sensor telemetry.

Pure functions that calculate realistic discharging battery levels, temperatures,
and dumpsys commands to apply them to an Android container.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, date, datetime


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


def generate_initial_battery_state(
    account_id: str | None = None,
    date: date | None = None,
    *,
    seed: int | str | None = None,
) -> BatteryTelemetryState:
    """Generate realistic initial discharging battery state (level 38..89%, temp 30.8..34.2°C)."""
    if seed is not None:
        draw_seed: int | str = seed
    elif account_id is not None:
        seed_date = date or datetime.now(UTC).date()
        draw_seed = f"battery:{account_id}:{seed_date.isoformat()}"
    else:
        seed_date = date or datetime.now(UTC).date()
        draw_seed = f"battery:telebuba:{seed_date.isoformat()}"
    rng = random.Random(draw_seed)  # noqa: S311  # nosec B311 - fixture telemetry, not crypto
    level = rng.randint(42, 86)
    temp_tenths_c = rng.randint(310, 338)  # 31.0°C .. 33.8°C
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


def build_sensor_jitter_commands(seed: int | str | None = None) -> list[str]:
    """Generate subtle sensor jitter around hand-holding angle."""
    rng = random.Random(seed)  # noqa: S311  # nosec B311 - fixture telemetry, not crypto
    ax = round(0.18 + rng.uniform(-0.09, 0.09), 4)
    ay = round(8.85 + rng.uniform(-0.14, 0.14), 4)
    az = round(4.15 + rng.uniform(-0.12, 0.12), 4)
    gx = round(rng.uniform(-0.025, 0.025), 5)
    gy = round(rng.uniform(-0.025, 0.025), 5)
    gz = round(rng.uniform(-0.015, 0.015), 5)
    return [
        f"setprop debug.sensors.accelerometer '{ax},{ay},{az}' 2>/dev/null || true",
        f"setprop debug.sensors.gyroscope '{gx},{gy},{gz}' 2>/dev/null || true",
    ]
