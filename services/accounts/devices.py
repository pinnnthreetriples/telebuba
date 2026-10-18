"""An account's second device: policy around the gateway's separate sign-in.

The gateway (``core.telegram_client.authorize_second_device``) performs one attempt
and undoes it on failure. What lives here is the policy: refuse what cannot work
before anything reaches Telegram, and pace code requests per account — every
attempt makes Telegram send the live account a login code, and repeated requests
are what earns "too many attempts" and flood waits.

The cooldown is process-local, like the rest of the login state: one uvicorn worker.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_account
from core.logging import log_event
from core.telegram_client import authorize_second_device, mobile_api_configured
from schemas.devices import DeviceRefusalCode, SecondDeviceSignIn
from services.accounts._result import AccountActionError, AccountNotFoundError

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.device_fingerprint import DeviceType

__all__ = [
    "DeviceRefusedError",
    "add_account_device",
    "assert_device_configured",
    "claim_code_request",
    "forget_device_cooldown",
    "second_session_name",
]

_LAST_CODE_REQUEST: dict[str, float] = {}
_PRESENT: DeviceRefusalCode = "device_already_present"
_NO_MOBILE_API: DeviceRefusalCode = "mobile_api_not_configured"
_COOLDOWN: DeviceRefusalCode = "device_code_cooldown"
# Refusals decided before any code was asked for: they leave the pacing untouched.
_NOT_REQUESTED: frozenset[DeviceRefusalCode] = frozenset(
    {
        "mobile_api_not_configured",
        "device_already_present",
        "device_phone_unknown",
        "device_primary_unavailable",
        "device_session_conflict",
    },
)


class DeviceRefusedError(AccountActionError):
    """A device could not be added; ``code`` is the stable code the SPA translates.

    An ``AccountActionError``, so the API answers it like every other refused account
    action: 400 with the code as ``message`` and a flood wait in ``fields``.
    """

    def __init__(self, code: DeviceRefusalCode, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(code, retry_after_seconds=retry_after_seconds)


def assert_device_configured(device_type: DeviceType) -> None:
    """Refuse a mobile authorization before anything is created when mobile is not set up."""
    if device_type == "mobile" and not mobile_api_configured():
        raise DeviceRefusedError(_NO_MOBILE_API)


def second_session_name(account: AccountRead, device_type: DeviceType) -> str:
    """The ``.session`` name of the account's second authorization: ``<own name>_<device>``."""
    return f"{account.session_name or account.account_id}_{device_type}"


def claim_code_request(account_id: str) -> None:
    """Pace login-code requests per account, or refuse with the seconds left.

    Stamped before the attempt: the request may reach Telegram even if it then fails.
    """
    now = time.monotonic()
    last = _LAST_CODE_REQUEST.get(account_id)
    if last is not None:
        left = int(last + settings.telegram.second_device_code_cooldown_seconds - now + 0.999)
        if left > 0:
            raise DeviceRefusedError(_COOLDOWN, retry_after_seconds=left)
    _LAST_CODE_REQUEST[account_id] = now


async def add_account_device(account_id: str, device_type: DeviceType) -> AccountRead:
    """Sign ``device_type`` in as the account's second authorization, or refuse with a code.

    Only by the operator's explicit request: nothing calls this on its own. With the
    emulator on, a mobile device then signs the official app in, in its pod.
    """
    account = await fetch_account(account_id)
    if account is None:
        raise AccountNotFoundError(account_id)
    if device_type in account.devices:
        raise DeviceRefusedError(_PRESENT)
    assert_device_configured(device_type)
    claim_code_request(account_id)
    result = await authorize_second_device(
        SecondDeviceSignIn(
            account_id=account_id,
            device_type=device_type,
            session_name=second_session_name(account, device_type),
        ),
    )
    if result.code in _NOT_REQUESTED:
        _LAST_CODE_REQUEST.pop(account_id, None)
    if result.code is not None:
        await log_event(
            "WARNING",
            "account_device_refused",
            account_id=account_id,
            extra={"device": device_type, "reason": result.code, "error_type": result.error_type},
        )
        raise DeviceRefusedError(result.code, retry_after_seconds=result.flood_wait_seconds)
    await log_event(
        "INFO", "account_device_added", account_id=account_id, extra={"device": device_type}
    )
    if device_type == "mobile" and settings.emulator.enabled:
        await _sign_in_mobile_app(account_id)
    saved = await fetch_account(account_id)
    if saved is None:
        raise AccountNotFoundError(account_id)
    return saved


async def _sign_in_mobile_app(account_id: str) -> None:
    """Sign the official app in, in the account's pod, on the new mobile fingerprint.

    The mobile device is already recorded: a pod that fails answers its own code
    (``open_account_mobile`` logs it) and the device stays, to be opened again from
    the account. The app's sign-in asks for a second login code, so the pacing
    stamp of the one just spent is dropped first.
    """
    # Deferred: ``mobile_emulator`` imports this module for its refusal and pacing.
    from services.accounts.mobile_emulator import open_account_mobile  # noqa: PLC0415

    _LAST_CODE_REQUEST.pop(account_id, None)
    await open_account_mobile(account_id)


def forget_device_cooldown(account_id: str) -> None:
    """Drop a deleted account's pacing entry."""
    _LAST_CODE_REQUEST.pop(account_id, None)
