"""Container stealth profiles and battery telemetry routines.

Extracted from ``adb.py`` to keep that module under the aislop file-size cap.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from core.emulator import adb
from core.emulator._battery import (
    BatteryTelemetryState,
    build_battery_telemetry_commands,
    generate_initial_battery_state,
)
from core.emulator._errors import EmulatorError
from core.emulator._profile import (
    build_battery_stealth_commands,
    build_cellular_stealth_commands,
    build_container_isolation_commands,
    build_usb_stealth_commands,
    generate_instance_boot_id,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_COMMAND_FAILED: EmulatorRefusalCode = "emulator_command_failed"


async def exec_root_script(serial: str, commands: Sequence[str]) -> str:
    """Execute a list of shell commands inside the container as root."""
    joined = " && ".join(commands)
    script = (
        f'if [ "$(id -u 2>/dev/null)" = "0" ]; then sh -c {shlex.quote(joined)}; '
        f"elif command -v su >/dev/null 2>&1; then su 0 sh -c {shlex.quote(joined)}; "
        f"else sh -c {shlex.quote(joined)}; fi"
    )
    return await adb.shell(serial, script)


async def apply_stealth_profile(
    serial: str,
    *,
    boot_id: str | None = None,
    battery_level: int | None = None,
    battery_temp: int | None = None,
) -> str:
    """Isolate container markers and simulate USB, battery and LTE, without ``resetprop``.

    redroid ships no ``resetprop``, so no ``ro.*`` property changes here: the user build
    goes into ``build.prop`` before boot (``core.emulator.redroid.build_prop``).
    """
    instance_boot_id = boot_id or generate_instance_boot_id()
    commands = [
        *build_container_isolation_commands(instance_boot_id),
        *build_usb_stealth_commands(),
        *build_battery_stealth_commands(level=battery_level, temp=battery_temp),
        *build_cellular_stealth_commands(),
    ]

    await exec_root_script(serial, commands)

    actual_boot_id = (await adb.shell(serial, "cat", "/proc/sys/kernel/random/boot_id")).strip()
    if actual_boot_id != instance_boot_id:
        raise EmulatorError(_EMULATOR_COMMAND_FAILED)

    probe = await adb.shell(serial, "echo ok")
    if probe.strip() != "ok":
        raise EmulatorError(_EMULATOR_COMMAND_FAILED)

    return instance_boot_id


async def apply_battery_telemetry(
    serial: str,
    state: BatteryTelemetryState | None = None,
    *,
    account_id: str | None = None,
    seed: int | str | None = None,
    now: datetime | None = None,
) -> BatteryTelemetryState:
    """Set the battery the phone has at ``now``: discharging by day, charging by night.

    ``now`` is the phone's own clock (its timezone), whose hour the level follows.
    """
    resolved_state = state or generate_initial_battery_state(
        account_id=account_id or (str(seed) if seed is not None else serial),
        now=now,
        seed=seed,
    )
    commands = build_battery_telemetry_commands(resolved_state)
    script: list[str] = []
    for cmd in commands:
        if script:
            script.append("&&")
        script.extend(cmd.split())
    await adb.shell(serial, *script)
    return resolved_state
