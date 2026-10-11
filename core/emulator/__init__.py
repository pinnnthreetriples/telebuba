"""Native Android emulator subsystem for Telebuba."""

from __future__ import annotations

from core.emulator.adb import ADBDriver
from core.emulator.config import (
    EmulatorAdbError,
    EmulatorError,
    EmulatorLaunchError,
    EmulatorNotFoundError,
    LoginCodeTimeoutError,
    NoProxyForMobileError,
    generate_device_profile,
    get_adb_path,
    get_apk_path,
    get_ldconsole_path,
    get_ldplayer_dir,
    is_emulator_available,
)
from core.emulator.ldconsole import (
    LDConsoleRunner,
    create_instance,
    get_default_runner,
    is_running,
    launch_instance,
    set_device_profile,
    set_resolution,
    set_resource_limits,
    stop_instance,
)
from core.emulator.session_capture import extract_verification_code, poll_for_login_code

__all__ = [
    "ADBDriver",
    "EmulatorAdbError",
    "EmulatorError",
    "EmulatorLaunchError",
    "EmulatorNotFoundError",
    "LDConsoleRunner",
    "LoginCodeTimeoutError",
    "NoProxyForMobileError",
    "create_instance",
    "extract_verification_code",
    "generate_device_profile",
    "get_adb_path",
    "get_apk_path",
    "get_default_runner",
    "get_ldconsole_path",
    "get_ldplayer_dir",
    "is_emulator_available",
    "is_running",
    "launch_instance",
    "poll_for_login_code",
    "set_device_profile",
    "set_resolution",
    "set_resource_limits",
    "stop_instance",
]
