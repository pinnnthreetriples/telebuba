"""The local Android container (LDPlayer 9) a mobile account is opened in.

- ``ldconsole`` — find / create / start / stop / remove containers
- ``adb``       — device-side commands, always through ``adb shell``
- ``ui_dump``   — the one parser of ``uiautomator dump`` and the screen classifier
- ``telegram_app`` — the Telegram app's screens, driven only through dumped nodes
- ``_profile``  — the container's identity from the mobile fingerprint (Luhn IMEI)
"""

from __future__ import annotations

from core.emulator._errors import EmulatorError
from core.emulator._profile import ContainerSpec, container_spec
from core.emulator.adb import assert_sdk, set_network, wait_for_boot
from core.emulator.ldconsole import (
    Instance,
    adb_serial,
    create_instance,
    find_instance,
    launch_instance,
    quit_instance,
    remove_container,
)
from core.emulator.telegram_app import (
    apply_account_proxy,
    confirm_number,
    ensure_app,
    enter_code,
    enter_password,
    enter_phone,
    press_start,
    read_screen,
    wait_screen,
)

__all__ = [
    "ContainerSpec",
    "EmulatorError",
    "Instance",
    "adb_serial",
    "apply_account_proxy",
    "assert_sdk",
    "confirm_number",
    "container_spec",
    "create_instance",
    "ensure_app",
    "enter_code",
    "enter_password",
    "enter_phone",
    "find_instance",
    "launch_instance",
    "press_start",
    "quit_instance",
    "read_screen",
    "remove_container",
    "set_network",
    "wait_for_boot",
    "wait_screen",
]
