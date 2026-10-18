"""The device farm: phones (mobile pods) listed, started, stopped and watched on the web."""

from __future__ import annotations

from services.device_farm._phones import list_phones, start_phone, stop_phone
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
    "list_phones",
    "parse_command",
    "screen_session",
    "start_phone",
    "stop_phone",
]
