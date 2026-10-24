"""The local Android container (redroid under Docker) a mobile account is opened in.

- ``pod``       — start / stop / remove a pod: the net-gateway and Android in its namespace
- ``redroid``   — find / create / start / show (scrcpy) Android containers
- ``scrcpy``    — the web screen: scrcpy-server on demand, one viewer's sockets
- ``adb``       — device-side commands, always through ``adb shell``
- ``ui_dump``   — the one parser of ``uiautomator dump`` and the screen classifier
- ``telegram_app`` — the Telegram app's screens, driven only through dumped nodes
- ``_seeder``   — a fresh container's contacts and camera roll, written once
- ``_seed_data`` — what it is seeded with: the country's people, the phone's photos
- ``_profile``  — the container's identity from the mobile fingerprint: a real phone's
  firmware (``_blueprints``) and the proxy country's region (``_geo``)
"""

from __future__ import annotations

from core.emulator._errors import EmulatorError
from core.emulator._input import HumanInputEngine, KeystrokeEvent, SwipeTrajectory, TapEvent
from core.emulator._profile import ContainerSpec, container_spec
from core.emulator._seeder import seed_device_if_needed
from core.emulator._text import escape_input_text
from core.emulator.adb import (
    BatteryTelemetryState,
    apply_battery_telemetry,
    apply_stealth_profile,
    assert_sdk,
    connect_device,
    human_swipe,
    human_tap,
    human_type,
    wait_for_boot,
)
from core.emulator.assets import ensure_telegram_apk
from core.emulator.playbook import TelegramMobilePlaybook
from core.emulator.pod import (
    StartedPod,
    pause_pod,
    pod_runs_proxy,
    remove_pod,
    start_pod,
    stop_paused_pod,
    stop_pod,
    stop_running_pods,
    unpause_pod,
)
from core.emulator.redroid import (
    Instance,
    adb_serial,
    find_instance,
    list_instances,
    show_instance,
    viewer_open,
)
from core.emulator.scrcpy import ScreenStream, open_screen
from core.emulator.telegram_app import (
    app_package,
    bring_app_to_front,
    confirm_number,
    ensure_app,
    enter_code,
    enter_password,
    enter_phone,
    leave_app,
    press_start,
    read_screen,
    wait_screen,
)

__all__ = [
    "BatteryTelemetryState",
    "ContainerSpec",
    "EmulatorError",
    "HumanInputEngine",
    "Instance",
    "KeystrokeEvent",
    "ScreenStream",
    "StartedPod",
    "SwipeTrajectory",
    "TapEvent",
    "TelegramMobilePlaybook",
    "adb_serial",
    "app_package",
    "apply_battery_telemetry",
    "apply_stealth_profile",
    "assert_sdk",
    "bring_app_to_front",
    "confirm_number",
    "connect_device",
    "container_spec",
    "ensure_app",
    "ensure_telegram_apk",
    "enter_code",
    "enter_password",
    "enter_phone",
    "escape_input_text",
    "find_instance",
    "human_swipe",
    "human_tap",
    "human_type",
    "leave_app",
    "list_instances",
    "open_screen",
    "pause_pod",
    "pod_runs_proxy",
    "press_start",
    "read_screen",
    "remove_pod",
    "seed_device_if_needed",
    "show_instance",
    "start_pod",
    "stop_paused_pod",
    "stop_pod",
    "stop_running_pods",
    "unpause_pod",
    "viewer_open",
    "wait_for_boot",
    "wait_screen",
]
