"""Drive the Telegram app inside a container, one confirmed screen at a time.

Every action first finds its target in the current UI dump and fails with
``emulator_field_missing`` when it is not there — nothing is typed into, or tapped
at, a place the screen did not show. Text is validated before the first keystroke.
The contacts permission is refused for good before the app first starts: Telegram never
syncs the phone's seeded contacts to the account.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
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
    deny_button,
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

_EMULATOR_COMMAND_FAILED: EmulatorRefusalCode = "emulator_command_failed"
_EMULATOR_FIELD_MISSING: EmulatorRefusalCode = "emulator_field_missing"
_EMULATOR_PHONE_INVALID: EmulatorRefusalCode = "emulator_phone_invalid"
# Typings of the cloud password, the second after clearing a box that showed a wrong count.
_PASSWORD_TRIES = 2
_WEB_PACKAGE = "org.telegram.messenger.web"
_PACKAGE_INSTALLER = "com.android.packageinstaller"
_PLAY_STORE_INSTALLER = "com.android.vending"
# The contacts group Telegram asks for: refused for good, so Android answers it alone.
_CONTACTS_PERMISSIONS = (
    "android.permission.READ_CONTACTS",
    "android.permission.WRITE_CONTACTS",
    "android.permission.GET_ACCOUNTS",
)
# The Telegram found on each phone, by serial, so a bring-to-front or a link needs no
# ``pm list packages``. A phone without one is never remembered, so ``ensure_app``'s
# install is seen at the next read; forgotten whenever a container starts
# (``forget_app``): a serial is a pod index's, and the next container there may be
# another phone.
_PACKAGES: dict[str, str] = {}
# How often each serial was forgotten: a lookup a new container overtook is not remembered.
_FORGOTTEN: dict[str, int] = {}

# Stdlib sink, codes and step names only (see ``core.emulator._errors``).
logger = logging.getLogger(__name__)


async def read_screen(serial: str) -> tuple[Screen, list[UiNode]]:
    """The screen now; ``unresponsive`` when the phone gave no dump at all."""
    xml = await adb.dump_ui(serial)
    if xml is None:
        return "unresponsive", []
    nodes = parse_dump(xml)
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
    generation = _FORGOTTEN.get(serial, 0)
    installed = await adb.installed_packages(serial)
    package = next((package for package in wanted if package in installed), None)
    if package is not None and _FORGOTTEN.get(serial, 0) == generation:
        _PACKAGES[serial] = package
    return package


def forget_app(serial: str) -> None:
    """Drop what ``app_package`` knows of ``serial``'s phone: it is read again next time."""
    _PACKAGES.pop(serial, None)
    _FORGOTTEN[serial] = _FORGOTTEN.get(serial, 0) + 1


async def bring_app_to_front(serial: str) -> None:
    package = await app_package(serial) or settings.emulator.telegram_package
    await adb.start_app(serial, package)


async def restart_app(serial: str) -> None:
    """Kill the app and launch it again: a frozen one comes back on its saved screen."""
    package = await app_package(serial) or settings.emulator.telegram_package
    await adb.force_stop(serial, package)
    await adb.start_app(serial, package)


async def go_back(serial: str) -> None:
    await adb.press(serial, adb.KEYCODE_BACK)


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
    await _deny_contacts(serial, package)
    await adb.start_app(serial, package)


async def _deny_contacts(serial: str, package: str) -> None:
    """Refuse the contacts for good; a phone that will not is denied on screen instead."""
    with suppress(EmulatorError):
        await adb.deny_permissions(serial, package, _CONTACTS_PERMISSIONS)
        return
    logger.warning("contacts permission not preset on %s", serial)


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


async def deny_permission(serial: str, nodes: list[UiNode]) -> None:
    """Tap the permission dialog's deny button, found by its resource-id."""
    await adb.human_tap(serial, _require(deny_button(nodes)))


async def put_off_contacts(serial: str, nodes: list[UiNode]) -> None:
    """Answer Telegram's contacts prompt «not now»: it does not ask again."""
    await adb.human_tap(serial, _require(find(nodes, labels().contacts_not_now)))


async def confirm_number(serial: str, nodes: list[UiNode]) -> None:
    await adb.human_tap(serial, _require(find(nodes, labels().yes)))


async def enter_code(serial: str, nodes: list[UiNode], code: str) -> None:
    escape_input_text(code)  # refuse untypeable text before touching the screen
    boxes = fields(nodes)
    await adb.human_tap(serial, _require(boxes[0] if boxes else None))
    await adb.human_type(serial, code, as_code=True)


async def enter_password(serial: str, nodes: list[UiNode], password: str) -> None:
    """Type the cloud password into the field the dump marks ``password``, then submit.

    Android dumps a password box as one mask character per typed one, so a dump taken
    after typing shows whether every character landed. A box that shows another count is
    emptied and typed once more; one still wrong is ``emulator_command_failed``, and
    nothing is submitted. A dump that does not show the box (or shows its hint) is no
    evidence: the password is submitted as typed. The password is never logged.
    """
    escape_input_text(password)  # refuse an untypeable password before touching the screen
    field = _require(password_field(nodes))
    shown: int | None = len(field.text) if _masked(field.text) else 0
    for _ in range(_PASSWORD_TRIES):
        await adb.human_tap(serial, field)
        if shown:
            await adb.press(serial, adb.KEYCODE_MOVE_END, *([adb.KEYCODE_DEL] * shown))
        await adb.human_type(serial, password, typo_probability=0.0)
        shown = await _password_length(serial)
        if shown in (None, len(password)):
            await adb.press(serial, adb.KEYCODE_ENTER)
            return
    raise EmulatorError(_EMULATOR_COMMAND_FAILED)


def _masked(text: str) -> bool:
    """Whether ``text`` is what a password box dumps: mask characters only (or nothing)."""
    return not any(char.isalnum() for char in text)


async def _password_length(serial: str) -> int | None:
    """How many characters the password box shows now; ``None`` when the dump cannot say."""
    xml = await adb.dump_ui(serial)
    field = password_field(parse_dump(xml)) if xml is not None else None
    # An empty box may be how this Android dumps a password at all: no evidence either.
    if field is None or not field.text or not _masked(field.text):
        return None
    return len(field.text)
