"""Phone-code login + session reset for the accounts domain.

Re-authorises an EXISTING account by phone code (request → submit, optional
2FA), plus logout and a full session reset. The ``phone_code_hash`` lives in an
in-memory TTL cache (single-worker, :mod:`._login_state`) between the two calls;
the ``.session`` file on disk carries the auth key, so a submit reconnects the
same session the code was requested on.

The gateway functions are imported at module scope so tests monkeypatch them at
``services.accounts.login.<name>`` — the public functions resolve those names
from module globals at call time (same convention as :mod:`.sessions`).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from core.config import settings
from core.db import DuplicateSessionNameError, fetch_account, update_account_from_session_check
from core.logging import log_event
from core.telegram_client import (
    log_out_session,
    remove_second_device,
    request_phone_code,
    submit_phone_code,
)
from schemas.accounts import AccountCreate
from schemas.device_fingerprint import TelegramClientRequest
from schemas.phone_login import PhoneCodeRequest, PhoneCodeRequestResult, PhoneCodeSubmit
from services.accounts._import_locks import import_lock
from services.accounts._login_state import forget_code, peek_code, remember_code
from services.accounts.devices import assert_device_configured
from services.accounts.lifecycle import add_account
from services.accounts.sessions import SessionAlreadyExistsError
from services.inbox_runtime import start_account_inbox, stop_account_inbox

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.device_fingerprint import DeviceType

__all__ = [
    "PhoneLoginError",
    "logout_account",
    "request_login_code",
    "reset_account_session",
    "start_phone_login",
    "submit_login_code",
]


class PhoneLoginError(ValueError):
    """A phone-login step the operator can act on (no phone, bad code, expired)."""


async def start_phone_login(
    phone: str,
    label: str | None = None,
    session_device: DeviceType = "desktop",
) -> AccountRead:
    """Provision a NEW account carrying only a phone number, ready for request-code.

    ``session_device`` is the device the login signs in as — desktop unless the
    operator picked mobile; a second device is added after this one is signed in.

    No ``.session`` file is written here — Telethon mints it on the first connect
    during ``request_login_code``, which runs after the operator assigns a proxy.
    The digits of the phone become both ``account_id`` and ``session_name`` (the
    ``AccountCreate`` pattern forbids the leading ``+``); re-adding the same phone
    surfaces as :class:`SessionAlreadyExistsError` the API maps to 409.

    Holds ``import_lock`` over the check-then-add for the same reason the two
    importers do, and it is not optional here: this is the THIRD writer of an
    account row, and ``_import_rollback`` deletes a row it finds present on the
    assumption that only a lock-holder could have created one under that key. A
    phone whose digits equal an uploaded session's stem (or a tdata ``user_id``)
    landing inside a failed import's window would otherwise have the operator's
    row deleted by that import's rollback. It also closes this function's own
    check-then-act race, which the ``DuplicateSessionNameError`` branch below was
    left to catch after the fact.
    """
    digits = "".join(ch for ch in phone if ch.isdigit())
    if not digits:
        msg = "Phone number must contain digits."
        raise PhoneLoginError(msg)
    assert_device_configured(session_device)
    async with import_lock(digits):
        if await fetch_account(digits) is not None:
            msg = f"An account for {phone} already exists."
            raise SessionAlreadyExistsError(msg)
        try:
            account = await add_account(
                AccountCreate(
                    account_id=digits,
                    session_name=digits,
                    phone=phone,
                    label=label,
                    session_device=session_device,
                ),
            )
        except DuplicateSessionNameError as exc:
            msg = f"An account for {phone} already exists."
            raise SessionAlreadyExistsError(msg) from exc
    await log_event("INFO", "phone_login_started", account_id=account.account_id)
    return account


async def request_login_code(account_id: str) -> PhoneCodeRequestResult:
    """Send a login code to an existing account's phone; cache the hash for submit."""
    account = await fetch_account(account_id)
    if account is None:
        msg = f"Unknown account: {account_id}"
        raise PhoneLoginError(msg)
    if not account.phone:
        msg = "No phone number on record — check the account first."
        raise PhoneLoginError(msg)
    assert_device_configured(account.session_device)
    challenge = await request_phone_code(
        PhoneCodeRequest(
            account_id=account_id,
            session_name=account.session_name,
            phone=account.phone,
        ),
    )
    if not challenge.phone_code_hash:
        raise PhoneLoginError(challenge.error or "Could not request a login code")
    remember_code(
        account_id,
        account.phone,
        challenge.phone_code_hash,
        now=time.monotonic(),
        ttl_seconds=settings.telegram.phone_code_ttl_seconds,
    )
    await log_event("INFO", "phone_code_requested", account_id=account_id)
    return PhoneCodeRequestResult(account_id=account_id, phone=account.phone)


async def submit_login_code(
    account_id: str,
    code: str,
    password: str | None = None,
) -> AccountRead:
    """Complete sign-in with the code (+ 2FA); persist the now-alive account."""
    account = await fetch_account(account_id)
    if account is None:
        msg = f"Unknown account: {account_id}"
        raise PhoneLoginError(msg)
    pending = peek_code(account_id, now=time.monotonic())
    if pending is None:
        msg = "No pending login code — request a new one."
        raise PhoneLoginError(msg)
    assert_device_configured(account.session_device)
    result = await submit_phone_code(
        PhoneCodeSubmit(
            account_id=account_id,
            session_name=account.session_name,
            phone=pending.phone,
            phone_code_hash=pending.phone_code_hash,
            code=code,
            password=password,
        ),
    )
    if result.status != "alive":
        raise PhoneLoginError(result.error_message or "Sign-in failed")
    forget_code(account_id)
    saved = await update_account_from_session_check(result)
    await start_account_inbox(saved.account_id)
    await log_event("INFO", "phone_login_success", account_id=account_id)
    return saved


async def logout_account(account_id: str) -> AccountRead:
    """Log the account out server-side and mark it unauthorized."""
    return await _end_session(account_id, wipe_session=False, event="account_logged_out")


async def reset_account_session(account_id: str) -> AccountRead:
    """Log out and wipe the local session token so the next login is clean."""
    return await _end_session(account_id, wipe_session=True, event="account_session_reset")


async def _end_session(account_id: str, *, wipe_session: bool, event: str) -> AccountRead:
    account = await fetch_account(account_id)
    if account is None:
        msg = f"Unknown account: {account_id}"
        raise PhoneLoginError(msg)
    assert_device_configured(account.session_device)
    await stop_account_inbox(account_id)
    try:
        result = await log_out_session(
            TelegramClientRequest(
                account_id=account_id,
                session_name=account.session_name,
                receive_updates=False,
            ),
            wipe_session=wipe_session,
        )
    except Exception:
        await start_account_inbox(account_id)
        raise
    forget_code(account_id)
    saved = await update_account_from_session_check(result)
    await log_event("INFO", event, account_id=account_id)
    # The account's other sign-in ends with it: it would otherwise keep serving warming.
    # After the status write, so a failure here cannot leave the row looking signed in.
    try:
        await remove_second_device(account_id)
    except Exception as exc:  # noqa: BLE001 - the account's own session has already ended.
        await log_event(
            "WARNING",
            "account_second_device_remove_failed",
            account_id=account_id,
            extra={"error_type": type(exc).__name__},
        )
        return saved
    return await fetch_account(account_id) or saved
