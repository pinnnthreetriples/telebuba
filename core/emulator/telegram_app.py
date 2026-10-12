"""Drive the Telegram app inside a container, one confirmed screen at a time.

Every action first finds its target in the current UI dump and fails with
``emulator_field_missing`` when it is not there — nothing is typed into, or tapped
at, a place the screen did not show. Text is validated before the first keystroke.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import phonenumbers

from core.config import settings
from core.db import fetch_account_proxy_settings
from core.emulator import adb
from core.emulator._errors import EmulatorError
from core.emulator._text import escape_input_text
from core.emulator.ui_dump import (
    CONNECT_PROXY_LABELS,
    START_LABELS,
    YES_LABELS,
    classify,
    fields,
    find,
    parse_dump,
    password_field,
)

if TYPE_CHECKING:
    from core.emulator.ui_dump import Screen, UiNode
    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_FIELD_MISSING: EmulatorRefusalCode = "emulator_field_missing"
_EMULATOR_PHONE_INVALID: EmulatorRefusalCode = "emulator_phone_invalid"
_EMULATOR_PROXY_MISSING: EmulatorRefusalCode = "emulator_proxy_missing"
_EMULATOR_PROXY_UNSUPPORTED: EmulatorRefusalCode = "emulator_proxy_unsupported"

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


async def _wait_for_button(serial: str, labels: frozenset[str]) -> UiNode:
    """The button labelled ``labels`` once it shows; ``emulator_field_missing`` if it never does."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.emulator.ui_timeout_seconds
    while True:
        _screen, nodes = await read_screen(serial)
        button = find(nodes, labels)
        if button is not None:
            return button
        if loop.time() >= deadline:
            raise EmulatorError(_EMULATOR_FIELD_MISSING)
        await asyncio.sleep(settings.emulator.poll_seconds)


def _require(node: UiNode | None) -> UiNode:
    if node is None:
        raise EmulatorError(_EMULATOR_FIELD_MISSING)
    return node


async def ensure_app(serial: str) -> None:
    """Install Telegram when the container lacks it, then start it."""
    package = settings.emulator.telegram_package
    if not await adb.package_installed(serial, package):
        await adb.install_apk(serial)
    await adb.start_app(serial, package)


async def apply_account_proxy(serial: str, account_id: str) -> None:
    """Point the app's own SOCKS5 setting at the account's proxy (``tg://socks``).

    Telegram keeps the setting, so it survives the container's window being closed.
    The credentials are read here, in core, and leave only inside the deep link.
    """
    proxy = await fetch_account_proxy_settings(account_id)
    if proxy is None:
        raise EmulatorError(_EMULATOR_PROXY_MISSING)
    if proxy.proxy_type != "socks5":
        raise EmulatorError(_EMULATOR_PROXY_UNSUPPORTED)
    query = {"server": proxy.host, "port": str(proxy.port)}
    if proxy.username:
        query["user"] = proxy.username
    if proxy.password:
        query["pass"] = proxy.password
    await adb.open_link(
        serial, f"tg://socks?{urlencode(query)}", settings.emulator.telegram_package
    )
    await adb.tap(serial, await _wait_for_button(serial, CONNECT_PROXY_LABELS))


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
    await adb.tap(serial, _require(find(nodes, START_LABELS)))


async def enter_phone(serial: str, nodes: list[UiNode], phone: str) -> None:
    """Fill the country-code box and the number box, then submit."""
    country_code, national = split_phone(phone)
    boxes = fields(nodes)
    if len(boxes) < 2:  # noqa: PLR2004 - country code + number
        raise EmulatorError(_EMULATOR_FIELD_MISSING)
    for box, value in ((boxes[0], country_code), (boxes[1], national)):
        await adb.tap(serial, box)
        await adb.press(serial, adb.KEYCODE_MOVE_END, *([adb.KEYCODE_DEL] * _CLEAR_KEYS))
        await adb.type_text(serial, value)
    await adb.press(serial, adb.KEYCODE_ENTER)


async def confirm_number(serial: str, nodes: list[UiNode]) -> None:
    await adb.tap(serial, _require(find(nodes, YES_LABELS)))


async def enter_code(serial: str, nodes: list[UiNode], code: str) -> None:
    boxes = fields(nodes)
    await adb.tap(serial, _require(boxes[0] if boxes else None))
    await adb.type_text(serial, code)


async def enter_password(serial: str, nodes: list[UiNode], password: str) -> None:
    """Type the cloud password into the field the dump marks ``password``, then submit."""
    escape_input_text(password)  # refuse an untypeable password before touching the screen
    await adb.tap(serial, _require(password_field(nodes)))
    await adb.type_text(serial, password)
    await adb.press(serial, adb.KEYCODE_ENTER)
