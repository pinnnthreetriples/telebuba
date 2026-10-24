"""The battery the health HAL reads: a power_supply tree, not redroid's zeros."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.emulator._battery import (
    BatteryTelemetryState,
    power_supply_files,
    write_power_supply,
)

if TYPE_CHECKING:
    from pathlib import Path

_DISCHARGING = BatteryTelemetryState(level=73, temp_tenths_c=318, voltage_mv=4102)


def test_the_battery_is_a_charged_li_ion_cell_in_good_health() -> None:
    files = power_supply_files(_DISCHARGING)

    assert files == {
        "battery/type": "Battery\n",
        "battery/present": "1\n",
        "battery/status": "Discharging\n",
        "battery/health": "Good\n",
        "battery/technology": "Li-ion\n",
        "battery/capacity": "73\n",
        "battery/voltage_now": "4102000\n",  # microvolts
        "battery/temp": "318\n",  # tenths of a degree
        "battery/charge_counter": "3285000\n",  # microampere-hours
        "battery/charge_full": "4500000\n",
        "battery/charge_full_design": "4500000\n",
    }


def test_the_status_follows_the_charger() -> None:
    charging = BatteryTelemetryState(
        level=60, temp_tenths_c=320, voltage_mv=4000, status=2, plugged=True
    )
    full = BatteryTelemetryState(level=100, temp_tenths_c=320, voltage_mv=4270, status=5)

    assert power_supply_files(charging)["battery/status"] == "Charging\n"
    assert power_supply_files(full)["battery/status"] == "Full\n"


def test_the_tree_is_written_and_rewritten(tmp_path: Path) -> None:
    write_power_supply(tmp_path, _DISCHARGING)
    write_power_supply(
        tmp_path, BatteryTelemetryState(level=40, temp_tenths_c=318, voltage_mv=3900)
    )

    assert (tmp_path / "battery" / "capacity").read_text(encoding="utf-8") == "40\n"
    assert (tmp_path / "battery" / "voltage_now").read_text(encoding="utf-8") == "3900000\n"
    assert (tmp_path / "battery" / "type").read_text(encoding="utf-8") == "Battery\n"
