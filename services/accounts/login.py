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
from core.db import fetch_account, update_account_from_session_check
from core.logging import log_event
from core.telegram_client import log_out_session, request_phone_code, submit_phone_code
from schemas.device_fingerprint import TelegramClientRequest
from schemas.phone_login import PhoneCodeRequest, PhoneCodeRequestResult, PhoneCodeSubmit
from services.accounts._login_state import forget_code, peek_code, remember_code

if TYPE_CHECKING:
    from schemas.accounts import AccountRead

__all__ = [
    "PhoneLoginError",
    "logout_account",
    "request_login_code",
    "reset_account_session",
    "submit_login_code",
]


class PhoneLoginError(ValueError):
    """A phone-login step the operator can act on (no phone, bad code, expired)."""


async def request_login_code(account_id: str) -> PhoneCodeRequestResult:
    """Send a login code to an existing account's phone; cache the hash for submit."""
    account = await fetch_account(account_id)
    if account is None:
        msg = f"Unknown account: {account_id}"
        raise PhoneLoginError(msg)
    if not account.phone:
        msg = "No phone number on record — check the account first."
        raise PhoneLoginError(msg)
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
    result = await log_out_session(
        TelegramClientRequest(
            account_id=account_id,
            session_name=account.session_name,
            receive_updates=False,
        ),
        wipe_session=wipe_session,
    )
    forget_code(account_id)
    saved = await update_account_from_session_check(result)
    await log_event("INFO", event, account_id=account_id)
    return saved
