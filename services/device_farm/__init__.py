"""The device farm: phones (mobile pods) listed, started, stopped and watched on the web."""

from __future__ import annotations

from services.device_farm._phones import (
    account_of,
    create_standalone_phone,
    delete_standalone_phone,
    list_phones,
    set_standalone_phone_proxy,
    start_phone,
    stop_phone,
)
from services.device_farm.screen import (
    ScreenCommandError,
    ScreenSession,
    ScreenSupersededError,
    parse_command,
    screen_session,
)

__all__ = [
    "ScreenCommandError",
    "ScreenSession",
    "ScreenSupersededError",
    "account_of",
    "create_standalone_phone",
    "delete_standalone_phone",
    "list_phones",
    "parse_command",
    "screen_session",
    "set_standalone_phone_proxy",
    "start_phone",
    "stop_phone",
]
