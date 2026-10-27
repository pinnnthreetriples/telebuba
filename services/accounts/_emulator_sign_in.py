"""Sign the container's Telegram app in, screen by confirmed screen.

The login code goes where Telegram sends it for a second device: the account's
EXISTING session (777000). The id of the newest 777000 message is taken BEFORE the
number is submitted, so only a code that arrives afterwards is typed — never an older
one, whatever the clocks say. Code requests share the per-account pacing of the
second-device sign-in. The result is what the screen shows at the end, nothing else:
the chat list, past the prompts a fresh sign-in shows over it (``_app_screen``), or a
refusal saying why not — never a silent "signed out". An app that freezes on the way
is restarted once and signed in again from its entry screen; a second code is asked
for only as the per-account pacing allows.
"""

from __future__ import annotations

from functools import partial
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
from services.accounts._app_screen import APP_UNRESPONSIVE, restarted_once, settled_screen
from services.accounts._mobile_progress import report_stage
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

# Where a sign-in may start, the prompts over a signed-in app's chat list included.
_ENTRY: set[Screen] = {"chats", "intro", "phone", "password", "permission", "contacts"}
# What a submitted code or password leads to.
_PAST_CODE: set[Screen] = {"chats", "password", "permission", "contacts"}
_SIGNED_IN: set[Screen] = {"chats"}


async def sign_in(account: AccountRead, serial: str) -> None:
    """Sign the app in until its chat list shows, or raise why it did not."""
    await restarted_once(account.account_id, serial, partial(_sign_in, account, serial))


async def _sign_in(account: AccountRead, serial: str) -> None:
    screen, nodes = await _wait(serial, _ENTRY)
    if screen == "intro":
        await press_start(serial, nodes)
        screen, nodes = await _wait(serial, {"phone"})
    if screen == "phone":
        screen, nodes = await _submit_phone_and_code(account, serial, nodes)
    if screen == "password":
        screen, nodes = await _submit_password(account, serial, nodes)
    if await settled_screen(serial, _SIGNED_IN, seen=(screen, nodes)) != "chats":
        raise EmulatorError(_EMULATOR_FIELD_MISSING)  # still on a sign-in screen


async def _wait(serial: str, wanted: set[Screen]) -> tuple[Screen, list[UiNode]]:
    """``wait_screen``, refused as unresponsive when the app gave no dump at the end."""
    screen, nodes = await wait_screen(serial, wanted)
    if screen == "unresponsive":
        raise EmulatorError(APP_UNRESPONSIVE)
    return screen, nodes


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
    report_stage(account.account_id, "phone")
    claim_code_request(account.account_id)
    cutoff = await _code_cutoff(account.account_id)
    await enter_phone(serial, nodes, account.phone)
    screen, nodes = await _wait(serial, {"confirm", "code", "password", "chats"})
    if screen == "confirm":
        await confirm_number(serial, nodes)
        screen, nodes = await _wait(serial, {"code", "password", "chats"})
    if screen != "code":
        raise EmulatorError(_EMULATOR_FIELD_MISSING)
    report_stage(account.account_id, "code")
    try:
        code = await wait_account_login_code(
            account.account_id,
            after_id=cutoff,
            wait_seconds=settings.emulator.code_wait_seconds,
            poll_seconds=settings.emulator.poll_seconds,
        )
    except TelegramClientPoolError as exc:
        raise DeviceRefusedError(_DEVICE_PRIMARY_UNAVAILABLE) from exc
    if code is None:
        raise EmulatorError(_EMULATOR_CODE_TIMEOUT)
    await enter_code(serial, nodes, code)
    return await _wait(serial, _PAST_CODE)


async def _submit_password(
    account: AccountRead,
    serial: str,
    nodes: list[UiNode],
) -> tuple[Screen, list[UiNode]]:
    report_stage(account.account_id, "password")
    password = await fetch_account_twofa_password(account.account_id)
    if not password:
        raise EmulatorError(_EMULATOR_TWOFA_MISSING)
    await enter_password(serial, nodes, password)
    return await _wait(serial, _PAST_CODE - {"password"})
