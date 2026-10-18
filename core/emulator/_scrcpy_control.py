"""scrcpy's control protocol (server 3.x): the viewer's commands as bytes, and back.

Only the commands the web screen offers are written; everything else scrcpy knows
(start an app, UHID, display power) has no way in. Big-endian throughout, as the
server reads it with a ``DataInputStream``.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING, assert_never

from schemas.device_farm import (
    ScreenClipboardGet,
    ScreenClipboardSet,
    ScreenKey,
    ScreenScroll,
    ScreenText,
    ScreenTouch,
)

if TYPE_CHECKING:
    import asyncio

    from schemas.device_farm import ScreenCommand

_INJECT_KEYCODE = 0
_INJECT_TEXT = 1
_INJECT_TOUCH = 2
_INJECT_SCROLL = 3
_GET_CLIPBOARD = 8
_SET_CLIPBOARD = 9

_KEY_ACTIONS = {"down": 0, "up": 1}
# MotionEvent.ACTION_DOWN / ACTION_UP / ACTION_MOVE.
_TOUCH_ACTIONS = {"down": 0, "up": 1, "move": 2}
# scrcpy's "generic finger": injected as a touchscreen finger, not as a mouse.
_FINGER_POINTER_ID = 0xFFFFFFFFFFFFFFFE
_FULL_PRESSURE = 0xFFFF
_COPY_KEY_NONE = 0
# No acknowledgement wanted for a clipboard set.
_NO_SEQUENCE = 0
# Scroll amounts travel as i16 fixed point over [-1, 1]; the server scales them by 16.
_SCROLL_SCALE = 16
_I16_MAX = 0x7FFF

# Device -> viewer messages on the control socket.
DEVICE_CLIPBOARD = 0
_DEVICE_ACK_CLIPBOARD = 1
_DEVICE_UHID_OUTPUT = 2


def _position(x: int, y: int, width: int, height: int) -> bytes:
    return struct.pack(">iiHH", x, y, width, height)


def _string(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack(">I", len(raw)) + raw


def _scroll_amount(value: float) -> int:
    return max(-_I16_MAX - 1, min(_I16_MAX, round(value / _SCROLL_SCALE * (_I16_MAX + 1))))


def encode_command(command: ScreenCommand) -> bytes:
    """The bytes scrcpy's control socket reads for ``command``."""
    match command:
        case ScreenTouch():
            pressure = 0 if command.action == "up" else _FULL_PRESSURE
            return (
                struct.pack(
                    ">BBQ", _INJECT_TOUCH, _TOUCH_ACTIONS[command.action], _FINGER_POINTER_ID
                )
                + _position(command.x, command.y, command.width, command.height)
                + struct.pack(">HII", pressure, 0, 0)
            )
        case ScreenScroll():
            return (
                struct.pack(">B", _INJECT_SCROLL)
                + _position(command.x, command.y, command.width, command.height)
                + struct.pack(">hhI", _scroll_amount(command.dx), _scroll_amount(command.dy), 0)
            )
        case ScreenKey():
            return struct.pack(
                ">BBIII",
                _INJECT_KEYCODE,
                _KEY_ACTIONS[command.action],
                command.keycode,
                0,
                command.meta,
            )
        case ScreenText():
            return struct.pack(">B", _INJECT_TEXT) + _string(command.text)
        case ScreenClipboardSet():
            return struct.pack(">BQB", _SET_CLIPBOARD, _NO_SEQUENCE, int(command.paste)) + _string(
                command.text
            )
        case ScreenClipboardGet():
            return struct.pack(">BB", _GET_CLIPBOARD, _COPY_KEY_NONE)
        case _:
            assert_never(command)


async def read_device_message(reader: asyncio.StreamReader) -> str | None:
    """The next clipboard text the device sends; ``None`` for any other message.

    Raises ``asyncio.IncompleteReadError`` once the socket is closed.
    """
    kind = (await reader.readexactly(1))[0]
    if kind == DEVICE_CLIPBOARD:
        (length,) = struct.unpack(">I", await reader.readexactly(4))
        return (await reader.readexactly(length)).decode("utf-8", errors="replace")
    if kind == _DEVICE_ACK_CLIPBOARD:
        await reader.readexactly(8)
        return None
    if kind == _DEVICE_UHID_OUTPUT:
        _id, length = struct.unpack(">HH", await reader.readexactly(4))
        await reader.readexactly(length)
        return None
    # An unknown message cannot be skipped: its length is unknown.
    raise ValueError(kind)
