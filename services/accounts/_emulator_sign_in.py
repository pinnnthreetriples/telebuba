"""Sign the container's Telegram app in, screen by confirmed screen.

The login code goes where Telegram sends it for a second device: the account's
EXISTING session (777000). The id of the newest 777000 message is taken BEFORE the
number is submitted, so only a code that arrives afterwards is typed — never an older
one, whatever the clocks say. Code requests share the per-account pacing of the
second-device sign-in. The result is what the screen shows at the end, nothing else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_account_twofa_password
from core.emulator import (
    EmulatorError,
    confirm_number,
    enter_code,
    enter_password,
    enter_phone,
    press_start,
    wait_screen,
)
from core.telegram_client import (
    TelegramClientPoolError,
    login_code_cutoff,
    wait_account_login_code,
)
from services.accounts.devices import DeviceRefusedError, claim_code_request

if TYPE_CHECKING:
    from core.emulator.ui_dump import Screen, UiNode
    from schemas.accounts import AccountRead
    from schemas.devices import DeviceRefusalCode
    from schemas.emulator import EmulatorRefusalCode

_DEVICE_PHONE_UNKNOWN: DeviceRefusalCode = "device_phone_unknown"
_DEVICE_PRIMARY_UNAVAILABLE: DeviceRefusalCode = "device_primary_unavailable"
_EMULATOR_CODE_TIMEOUT: EmulatorRefusalCode = "emulator_code_timeout"
_EMULATOR_FIELD_MISSING: EmulatorRefusalCode = "emulator_field_missing"
_EMULATOR_TWOFA_MISSING: EmulatorRefusalCode = "emulator_twofa_missing"

_ENTRY: set[Screen] = {"chats", "intro", "phone", "password"}


async def sign_in(account: AccountRead, serial: str) -> bool:
    """``True`` only when the chat list is on screen at the end."""
    screen, nodes = await wait_screen(serial, _ENTRY)
    if screen == "intro":
        await press_start(serial, nodes)
        screen, nodes = await wait_screen(serial, {"phone"})
    if screen == "phone":
        screen, nodes = await _submit_phone_and_code(account, serial, nodes)
    if screen == "password":
        screen, nodes = await _submit_password(account, serial, nodes)
    return screen == "chats"


async def _code_cutoff(account_id: str) -> int:
    try:
        return await login_code_cutoff(account_id)
    except TelegramClientPoolError as exc:
        raise DeviceRefusedError(_DEVICE_PRIMARY_UNAVAILABLE) from exc


async def _submit_phone_and_code(
    account: AccountRead,
    serial: str,
    nodes: list[UiNode],
) -> tuple[Screen, list[UiNode]]:
    if not account.phone:
        raise DeviceRefusedError(_DEVICE_PHONE_UNKNOWN)
    claim_code_request(account.account_id)
    cutoff = await _code_cutoff(account.account_id)
    await enter_phone(serial, nodes, account.phone)
    screen, nodes = await wait_screen(serial, {"confirm", "code", "password", "chats"})
    if screen == "confirm":
        await confirm_number(serial, nodes)
        screen, nodes = await wait_screen(serial, {"code", "password", "chats"})
    if screen != "code":
        raise EmulatorError(_EMULATOR_FIELD_MISSING)
    code = await wait_account_login_code(
        account.account_id,
        after_id=cutoff,
        wait_seconds=settings.emulator.code_wait_seconds,
        poll_seconds=settings.emulator.poll_seconds,
    )
    if code is None:
        raise EmulatorError(_EMULATOR_CODE_TIMEOUT)
    await enter_code(serial, nodes, code)
    return await wait_screen(serial, {"password", "chats"})


async def _submit_password(
    account: AccountRead,
    serial: str,
    nodes: list[UiNode],
) -> tuple[Screen, list[UiNode]]:
    password = await fetch_account_twofa_password(account.account_id)
    if not password:
        raise EmulatorError(_EMULATOR_TWOFA_MISSING)
    await enter_password(serial, nodes, password)
    return await wait_screen(serial, {"chats"})
