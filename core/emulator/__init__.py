"""The local Android container (redroid under Docker) a mobile account is opened in.

- ``redroid``   — find / create / start / show (scrcpy) / stop / remove containers
- ``adb``       — device-side commands, always through ``adb shell``
- ``ui_dump``   — the one parser of ``uiautomator dump`` and the screen classifier
- ``telegram_app`` — the Telegram app's screens, driven only through dumped nodes
- ``_profile``  — the container's identity from the mobile fingerprint: a real phone's
  firmware (``_blueprints``) and the proxy country's region (``_geo``)
"""

from __future__ import annotations

from core.emulator._errors import EmulatorError
from core.emulator._profile import ContainerSpec, container_spec
from core.emulator._text import escape_input_text
from core.emulator.adb import assert_sdk, connect_device, set_network, wait_for_boot
from core.emulator.redroid import (
    Instance,
    adb_serial,
    create_instance,
    find_instance,
    launch_instance,
    quit_instance,
    refresh_identity,
    remove_container,
    set_container_network,
    show_instance,
)
from core.emulator.telegram_app import (
    apply_account_proxy,
    bring_app_to_front,
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
    "bring_app_to_front",
    "confirm_number",
    "connect_device",
    "container_spec",
    "create_instance",
    "ensure_app",
    "enter_code",
    "enter_password",
    "enter_phone",
    "escape_input_text",
    "find_instance",
    "launch_instance",
    "press_start",
    "quit_instance",
    "read_screen",
    "refresh_identity",
    "remove_container",
    "set_container_network",
    "set_network",
    "show_instance",
    "wait_for_boot",
    "wait_screen",
]
