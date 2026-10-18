"""Drive the Telegram app inside a container, one confirmed screen at a time.

Every action first finds its target in the current UI dump and fails with
``emulator_field_missing`` when it is not there — nothing is typed into, or tapped
at, a place the screen did not show. Text is validated before the first keystroke.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import phonenumbers

from core.config import settings
from core.emulator import adb
from core.emulator._errors import EmulatorError
from core.emulator._text import escape_input_text
from core.emulator.ui_dump import (
    classify,
    fields,
    find,
    labels,
    parse_dump,
    password_field,
)

if TYPE_CHECKING:
    from core.emulator.ui_dump import Screen, UiNode
    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_FIELD_MISSING: EmulatorRefusalCode = "emulator_field_missing"
_EMULATOR_PHONE_INVALID: EmulatorRefusalCode = "emulator_phone_invalid"

# Enough DELs to empty Telegram's country-code box (at most four digits).
_CLEAR_KEYS = 6


async def read_screen(serial: str) -> tuple[Screen, list[UiNode]]:
    nodes = parse_dump(await adb.dump_ui(serial))
    return classify(nodes), nodes


async def wait_screen(serial: str, wanted: set[Screen]) -> tuple[Screen, list[UiNode]]:
    """Poll until one of ``wanted`` shows; the last screen seen when time runs out."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.emulator.ui_timeout_seconds
    while True:
        screen, nodes = await read_screen(serial)
        if screen in wanted or loop.time() >= deadline:
            return screen, nodes
        await asyncio.sleep(settings.emulator.poll_seconds)


def _require(node: UiNode | None) -> UiNode:
    if node is None:
        raise EmulatorError(_EMULATOR_FIELD_MISSING)
    return node


async def bring_app_to_front(serial: str) -> None:
    await adb.start_app(serial, settings.emulator.telegram_package)


async def leave_app(serial: str) -> None:
    """Send the app to the background with HOME, as a person does when done with it."""
    await adb.press(serial, adb.KEYCODE_HOME)


async def ensure_app(serial: str) -> None:
    """Install Telegram when the container lacks it, then start it."""
    package = settings.emulator.telegram_package
    if not await adb.package_installed(serial, package):
        await adb.install_apk(serial)
    await adb.start_app(serial, package)


def split_phone(phone: str) -> tuple[str, str]:
    """``(country code, national number)``, or ``emulator_phone_invalid``."""
    try:
        parsed = phonenumbers.parse(phone if phone.startswith("+") else f"+{phone}")
    except phonenumbers.NumberParseException as exc:
        raise EmulatorError(_EMULATOR_PHONE_INVALID) from exc
    national = phonenumbers.national_significant_number(parsed)
    if not parsed.country_code or not national:
        raise EmulatorError(_EMULATOR_PHONE_INVALID)
    return str(parsed.country_code), national


async def press_start(serial: str, nodes: list[UiNode]) -> None:
    await adb.human_tap(serial, _require(find(nodes, labels().start)))


async def enter_phone(serial: str, nodes: list[UiNode], phone: str) -> None:
    """Fill the country-code box and the number box, then submit."""
    country_code, national = split_phone(phone)
    boxes = fields(nodes)
    if len(boxes) < 2:  # noqa: PLR2004 - country code + number
        raise EmulatorError(_EMULATOR_FIELD_MISSING)
    for box, value in ((boxes[0], country_code), (boxes[1], national)):
        await adb.human_tap(serial, box)
        await adb.press(serial, adb.KEYCODE_MOVE_END, *([adb.KEYCODE_DEL] * _CLEAR_KEYS))
        await adb.human_type(serial, value)
    await adb.press(serial, adb.KEYCODE_ENTER)


async def confirm_number(serial: str, nodes: list[UiNode]) -> None:
    await adb.human_tap(serial, _require(find(nodes, labels().yes)))


async def enter_code(serial: str, nodes: list[UiNode], code: str) -> None:
    escape_input_text(code)  # refuse untypeable text before touching the screen
    boxes = fields(nodes)
    await adb.human_tap(serial, _require(boxes[0] if boxes else None))
    await adb.human_type(serial, code, typo_probability=0.0)


async def enter_password(serial: str, nodes: list[UiNode], password: str) -> None:
    """Type the cloud password into the field the dump marks ``password``, then submit."""
    escape_input_text(password)  # refuse an untypeable password before touching the screen
    await adb.human_tap(serial, _require(password_field(nodes)))
    await adb.human_type(serial, password, typo_probability=0.0)
    await adb.press(serial, adb.KEYCODE_ENTER)
