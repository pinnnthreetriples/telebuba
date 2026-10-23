"""Drive the Telegram app inside a container, one confirmed screen at a time.

Every action first finds its target in the current UI dump and fails with
``emulator_field_missing`` when it is not there — nothing is typed into, or tapped
at, a place the screen did not show. Text is validated before the first keystroke.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import phonenumbers

from core.android_apk import app_identity, find_store_apk, local_apk_identity
from core.config import settings
from core.emulator import adb
from core.emulator._errors import EmulatorError
from core.emulator._text import escape_input_text
from core.emulator.assets import ensure_telegram_apk
from core.emulator.ui_dump import (
    classify,
    fields,
    find,
    labels,
    parse_dump,
    password_field,
)

if TYPE_CHECKING:
    from pathlib import Path

    from core.emulator.ui_dump import Screen, UiNode
    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_FIELD_MISSING: EmulatorRefusalCode = "emulator_field_missing"
_EMULATOR_PHONE_INVALID: EmulatorRefusalCode = "emulator_phone_invalid"
_WEB_PACKAGE = "org.telegram.messenger.web"
_PACKAGE_INSTALLER = "com.android.packageinstaller"
_PLAY_STORE_INSTALLER = "com.android.vending"
# The Telegram found on each phone, by serial, so a bring-to-front or a link needs no
# ``pm list packages``. A phone without one is never remembered, so ``ensure_app``'s
# install is seen at the next read; forgotten whenever a container starts
# (``forget_app``): a serial is a pod index's, and the next container there may be
# another phone.
_PACKAGES: dict[str, str] = {}


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


async def app_package(serial: str) -> str | None:
    """The Telegram installed on the phone: the configured package, else the APK's.

    A phone set up from an older APK keeps its app (and its sign-in): the package is
    read off the phone, never assumed from whichever APK is on the host now.
    """
    if (known := _PACKAGES.get(serial)) is not None:
        return known
    apk = local_apk_identity()
    wanted = [settings.emulator.telegram_package, *([apk.package] if apk else [])]
    installed = await adb.installed_packages(serial)
    package = next((package for package in wanted if package in installed), None)
    if package is not None:
        _PACKAGES[serial] = package
    return package


def forget_app(serial: str) -> None:
    """Drop what ``app_package`` knows of ``serial``'s phone: it is read again next time."""
    _PACKAGES.pop(serial, None)


async def bring_app_to_front(serial: str) -> None:
    package = await app_package(serial) or settings.emulator.telegram_package
    await adb.start_app(serial, package)


async def leave_app(serial: str) -> None:
    """Send the app to the background with HOME, as a person does when done with it."""
    await adb.press(serial, adb.KEYCODE_HOME)


async def ensure_app(serial: str, *, apk_path: Path | None = None) -> None:
    """Install Telegram when the container lacks it (fetching the APK first), then start it."""
    package = await app_package(serial)
    if package is None:
        target_apk = apk_path or find_store_apk(
            settings.emulator.telegram_package,
            version=settings.emulator.telegram_app_version or None,
        )
        if target_apk is None and settings.emulator.telegram_package != _WEB_PACKAGE:
            target_apk = find_store_apk(
                _WEB_PACKAGE,
                version=settings.emulator.telegram_app_version or None,
            )
        if target_apk is None:
            package = (await ensure_telegram_apk()).package
            target_apk = settings.emulator.telegram_apk_path
        else:
            package = app_identity(target_apk).package
        installer = _PACKAGE_INSTALLER if package == _WEB_PACKAGE else _PLAY_STORE_INSTALLER
        await adb.install_apk(serial, target_apk, installer_package=installer)
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
    """Empty the number box, then the country-code box; fill them in order and submit.

    A DEL in Telegram's empty number box goes back and deletes from the country code,
    so the number box is emptied first and only by as many DELs as it shows characters;
    one too many lands in the country code, which is emptied next anyway.
    """
    country_code, national = split_phone(phone)
    boxes = fields(nodes)
    if len(boxes) < 2:  # noqa: PLR2004 - country code + number
        raise EmulatorError(_EMULATOR_FIELD_MISSING)
    country, number = boxes[0], boxes[1]
    for box in (number, country):
        if box.text:
            await adb.human_tap(serial, box)
            await adb.press(serial, adb.KEYCODE_MOVE_END, *([adb.KEYCODE_DEL] * len(box.text)))
    for box, value in ((country, country_code), (number, national)):
        await adb.human_tap(serial, box)
        await adb.human_type(serial, value)
    await adb.press(serial, adb.KEYCODE_ENTER)


async def confirm_number(serial: str, nodes: list[UiNode]) -> None:
    await adb.human_tap(serial, _require(find(nodes, labels().yes)))


async def enter_code(serial: str, nodes: list[UiNode], code: str) -> None:
    escape_input_text(code)  # refuse untypeable text before touching the screen
    boxes = fields(nodes)
    await adb.human_tap(serial, _require(boxes[0] if boxes else None))
    await adb.human_type(serial, code, as_code=True)


async def enter_password(serial: str, nodes: list[UiNode], password: str) -> None:
    """Type the cloud password into the field the dump marks ``password``, then submit."""
    escape_input_text(password)  # refuse an untypeable password before touching the screen
    await adb.human_tap(serial, _require(password_field(nodes)))
    await adb.human_type(serial, password, typo_probability=0.0)
    await adb.press(serial, adb.KEYCODE_ENTER)
