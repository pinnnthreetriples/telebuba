"""Android emulator (LDPlayer 9 + ADB) and 777000 session-capture package."""

from __future__ import annotations

from core.emulator.adb import (
    KEYCODE_BACK,
    KEYCODE_ENTER,
    clear_http_proxy,
    configure_http_proxy,
    dump_ui_xml,
    input_text,
    press_key,
    start_app,
    tap,
    wait_for_boot,
    wait_for_text,
)
from core.emulator.config import EmulatorSettings, emulator_settings
from core.emulator.ldconsole import (
    EmulatorCommandError,
    EmulatorError,
    EmulatorNotFoundError,
    create_instance,
    deterministic_hardware_ids,
    is_running,
    launch_instance,
    set_device_profile,
    set_resolution,
    set_resource_limits,
    stop_instance,
)
from core.emulator.session_capture import (
    TELEGRAM_SERVICE_USER_ID,
    LoginCodeTimeoutError,
    extract_login_code,
    wait_for_telegram_login_code,
)

__all__ = [
    "KEYCODE_BACK",
    "KEYCODE_ENTER",
    "TELEGRAM_SERVICE_USER_ID",
    "EmulatorCommandError",
    "EmulatorError",
    "EmulatorNotFoundError",
    "EmulatorSettings",
    "LoginCodeTimeoutError",
    "clear_http_proxy",
    "configure_http_proxy",
    "create_instance",
    "deterministic_hardware_ids",
    "dump_ui_xml",
    "emulator_settings",
    "extract_login_code",
    "input_text",
    "is_running",
    "launch_instance",
    "press_key",
    "set_device_profile",
    "set_resolution",
    "set_resource_limits",
    "start_app",
    "stop_instance",
    "tap",
    "wait_for_boot",
    "wait_for_telegram_login_code",
    "wait_for_text",
]
