"""Sign the container's Telegram app in, screen by confirmed screen.

The login code goes where Telegram sends it for a second device: the account's
EXISTING session (777000). The id of the newest 777000 message is taken BEFORE the
number is submitted, so only a code that arrives afterwards is typed — never an older
one, whatever the clocks say. Code requests share the per-account pacing of the
second-device sign-in. The result is what the screen shows at the end, nothing else:
the chat list, past the prompts a fresh sign-in shows over it (``_app_screen``), or a
refusal saying why not — never a silent "signed out". An app that freezes on the way
is restarted (twice at most) and signed in again from its entry screen; a second code is
asked for only as the per-account pacing allows. An app left on its number confirmation
or code screen (a sign-in started by hand) is taken back to the phone screen with BACK
and signed in from there. A cloud password Telegram does not take is a refusal of its
own: the operator is needed, and no further code is requested for it.
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
    go_back,
    press_start,
    wait_screen,
)
from core.telegram_client import (
    TelegramClientPoolError,
    login_code_cutoff,
    wait_account_login_code,
)
from services.accounts._app_screen import APP_UNRESPONSIVE, restarted_if_frozen, settled_screen
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
_EMULATOR_PASSWORD_REJECTED: EmulatorRefusalCode = "emulator_password_rejected"  # noqa: S105 - a refusal code
_EMULATOR_TWOFA_MISSING: EmulatorRefusalCode = "emulator_twofa_missing"

# Where a sign-in may start, the prompts over a signed-in app's chat list included.
_ENTRY: set[Screen] = {
    "chats",
    "intro",
    "phone",
    "confirm",
    "code",
    "password",
    "permission",
    "contacts",
}
# BACK presses from a confirmation or code screen to the phone screen, at most.
_BACKS_TO_PHONE = 3
# What a submitted code or password leads to.
_PAST_CODE: set[Screen] = {"chats", "password", "permission", "contacts"}
_SIGNED_IN: set[Screen] = {"chats"}


async def sign_in(account: AccountRead, serial: str) -> None:
    """Sign the app in until its chat list shows, or raise why it did not."""
    await restarted_if_frozen(account.account_id, serial, partial(_sign_in, account, serial))


async def _sign_in(account: AccountRead, serial: str) -> None:
    screen, nodes = await _wait(serial, _ENTRY)
    if screen in {"confirm", "code"}:
        screen, nodes = await _back_to_phone(serial)
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


async def _back_to_phone(serial: str) -> tuple[Screen, list[UiNode]]:
    """BACK from the confirmation or code screen until the phone (or intro) screen shows."""
    for _ in range(_BACKS_TO_PHONE):
        await go_back(serial)
        screen, nodes = await _wait(serial, {"intro", "phone"})
        if screen in {"intro", "phone"}:
            return screen, nodes
    raise EmulatorError(_EMULATOR_FIELD_MISSING)  # BACK did not leave it


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
    screen, nodes = await _wait(serial, _PAST_CODE - {"password"})
    if screen == "password":
        # Telegram empties the field of a rejected password; waiting longer or asking for
        # another code would not change that, and the operator has to look.
        raise EmulatorError(_EMULATOR_PASSWORD_REJECTED)
    return screen, nodes
