"""scrcpy's control protocol: the exact bytes the 3.x server's ``ControlMessageReader`` reads."""

from __future__ import annotations

import asyncio
import struct

import pytest

from core.emulator._scrcpy_control import encode_command, read_device_message
from schemas.device_farm import (
    ScreenClipboardGet,
    ScreenClipboardSet,
    ScreenKey,
    ScreenScroll,
    ScreenText,
    ScreenTouch,
)


def test_a_touch_is_a_generic_finger_at_the_frame_point() -> None:
    down = encode_command(
        ScreenTouch(type="touch", action="down", x=100, y=200, width=720, height=1280)
    )
    up = encode_command(
        ScreenTouch(type="touch", action="up", x=100, y=200, width=720, height=1280)
    )

    assert len(down) == 32
    assert down == (
        bytes([2, 0])
        + (0xFFFFFFFFFFFFFFFE).to_bytes(8, "big")
        + struct.pack(">iiHH", 100, 200, 720, 1280)
        + struct.pack(">HII", 0xFFFF, 0, 0)
    )
    assert up[1] == 1, "ACTION_UP"
    assert up[22:24] == b"\x00\x00", "a lifted finger has no pressure"


def test_a_move_is_action_move() -> None:
    move = encode_command(ScreenTouch(type="touch", action="move", x=1, y=2, width=3, height=4))

    assert move[1] == 2


def test_a_key_carries_its_keycode_and_meta_state() -> None:
    raw = encode_command(ScreenKey(type="key", action="up", keycode=4, meta=1))

    assert raw == struct.pack(">BBIII", 0, 1, 4, 0, 1)


def test_text_is_utf8_with_a_four_byte_length() -> None:
    raw = encode_command(ScreenText(type="text", text="héllo"))

    assert raw == b"\x01" + struct.pack(">I", 6) + "héllo".encode()


def test_a_scroll_is_i16_fixed_point_over_sixteen() -> None:
    raw = encode_command(
        ScreenScroll(type="scroll", x=10, y=20, width=720, height=1280, dx=0, dy=-16)
    )

    assert len(raw) == 21
    assert raw[0] == 3
    assert struct.unpack(">hhI", raw[13:]) == (0, -32768, 0)
    full_up = encode_command(ScreenScroll(type="scroll", x=0, y=0, width=1, height=1, dx=16, dy=1))
    assert struct.unpack(">hh", full_up[13:17]) == (32767, 2048)


def test_clipboard_set_asks_for_no_acknowledgement() -> None:
    raw = encode_command(ScreenClipboardSet(type="clipboard_set", text="код", paste=True))

    assert raw == b"\x09" + bytes(8) + b"\x01" + struct.pack(">I", 6) + "код".encode()


def test_clipboard_get_copies_nothing_first() -> None:
    assert encode_command(ScreenClipboardGet(type="clipboard_get")) == b"\x08\x00"


def _reader(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


@pytest.mark.asyncio
async def test_the_device_clipboard_is_read_back() -> None:
    reader = _reader(b"\x00" + struct.pack(">I", 3) + b"abc")

    assert await read_device_message(reader) == "abc"


@pytest.mark.asyncio
async def test_acks_and_uhid_output_are_skipped_whole() -> None:
    reader = _reader(
        b"\x01"
        + bytes(8)
        + b"\x02"
        + struct.pack(">HH", 1, 2)
        + b"zz"
        + b"\x00"
        + struct.pack(">I", 1)
        + b"x"
    )

    assert await read_device_message(reader) is None
    assert await read_device_message(reader) is None
    assert await read_device_message(reader) == "x"


@pytest.mark.asyncio
async def test_an_unknown_device_message_cannot_be_skipped() -> None:
    with pytest.raises(ValueError, match="9"):
        await read_device_message(_reader(b"\x09"))


@pytest.mark.asyncio
async def test_a_closed_socket_ends_the_reading() -> None:
    with pytest.raises(asyncio.IncompleteReadError):
        await read_device_message(_reader(b"\x00\x00"))
