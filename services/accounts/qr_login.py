"""QR-code login for the accounts domain: scan from a signed-in phone, save the account.

The flow mirrors the phone-code one so the first Telegram connection goes through
the proxy the operator assigns: ``start_qr_login`` only creates a placeholder
account (no phone, no connection); ``begin_qr_login`` runs after the proxy step and
asks the gateway for a QR; the SPA then polls ``get_qr_login`` until the phone
accepted it (and, for a 2FA account, ``submit_qr_password`` answered the prompt).

The placeholder keeps its ``qr-<random>`` id forever. Once signed in,
``update_account_from_session_check`` fills the user id, phone and names, which is
what the UI shows. When the scanned account turns out to be one Telebuba already
has, the new authorization is revoked and the placeholder dropped.

The gateway functions are imported at module scope so tests monkeypatch them at
``services.accounts.qr_login.<name>`` (same convention as :mod:`.login`).
"""

from __future__ import annotations

import asyncio
import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from core.db import (
    DuplicateSessionNameError,
    fetch_account,
    list_accounts,
    update_account_from_session_check,
)
from core.logging import log_event
from core.telegram_client import (
    QrLoginGatewayError,
    forget_qr_login,
    log_out_session,
    read_qr_login,
)
from core.telegram_client import (
    cancel_qr_login as _gateway_cancel,
)
from core.telegram_client import (
    start_qr_login as _gateway_start,
)
from core.telegram_client import (
    submit_qr_password as _gateway_submit,
)
from schemas.accounts import AccountCreate
from schemas.device_fingerprint import TelegramClientRequest
from schemas.qr_login import QrLoginErrorCode, QrLoginRequest, QrLoginState, QrLoginView
from services.accounts._import_locks import import_lock
from services.accounts._import_rollback import discard_imported_session
from services.accounts.devices import assert_device_configured
from services.accounts.lifecycle import add_account
from services.inbox_runtime import start_account_inbox

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.device_fingerprint import DeviceType

__all__ = [
    "QrLoginError",
    "begin_qr_login",
    "cancel_qr_login",
    "get_qr_login",
    "start_qr_login",
    "submit_qr_password",
]

# One lock per account so two polls that both see "alive" persist it once. Created
# lazily and never freed: bounded by the accounts that ever signed in by QR.
_FINISH_LOCKS: dict[str, asyncio.Lock] = {}
# The saved account of every finished sign-in, so a poll after the first answers
# the same account instead of "no QR login".
_FINISHED: dict[str, AccountRead] = {}
_UNKNOWN_ACCOUNT: QrLoginErrorCode = "unknown_account"
_ALREADY_SIGNED_IN: QrLoginErrorCode = "already_signed_in"
_ACCOUNT_EXISTS: QrLoginErrorCode = "account_exists"
_NO_QR_LOGIN: QrLoginErrorCode = "no_qr_login"


class QrLoginError(ValueError):
    """A QR-login step the operator can act on; ``code`` is the stable reason."""

    def __init__(self, code: QrLoginErrorCode) -> None:
        super().__init__(code)
        self.code: QrLoginErrorCode = code


async def start_qr_login(
    label: str | None = None,
    session_device: DeviceType = "desktop",
) -> AccountRead:
    """Provision a NEW placeholder account, ready for ``begin_qr_login``.

    No ``.session`` file and no connection yet: Telethon mints the session on the
    first connect, which runs after the operator assigns a proxy. Holds
    ``import_lock`` over the insert for the reason ``login.start_phone_login``
    does (``_import_rollback`` deletes a row it finds under a locked key).
    """
    assert_device_configured(session_device)
    account_id = f"qr-{secrets.token_hex(6)}"
    async with import_lock(account_id):
        try:
            account = await add_account(
                AccountCreate(
                    account_id=account_id,
                    session_name=account_id,
                    phone=None,
                    label=label,
                    session_device=session_device,
                ),
            )
        except DuplicateSessionNameError as exc:
            # A random 48-bit id colliding is not a real case; surface it as a refusal.
            raise QrLoginError(_ACCOUNT_EXISTS) from exc
    await log_event("INFO", "qr_login_started", account_id=account.account_id)
    return account


async def begin_qr_login(account_id: str) -> QrLoginView:
    """Open (or restart) the account's QR sign-in and return the first QR."""
    account = await fetch_account(account_id)
    if account is None:
        raise QrLoginError(_UNKNOWN_ACCOUNT)
    if account.status == "alive":
        raise QrLoginError(_ALREADY_SIGNED_IN)
    assert_device_configured(account.session_device)
    # Accounts Telebuba already holds: Telegram must not offer them this QR.
    ignored = [
        other.user_id
        for other in (await list_accounts()).accounts
        if other.account_id != account_id and other.user_id is not None
    ]
    state = await _gateway_start(
        QrLoginRequest(
            account_id=account_id,
            session_name=account.session_name,
            ignored_user_ids=ignored,
        ),
    )
    return await _view(state)


async def get_qr_login(account_id: str) -> QrLoginView:
    """The QR sign-in's current state; persists the account the first time it is alive."""
    state = read_qr_login(account_id)
    if state is None:
        finished = _FINISHED.get(account_id)
        # A finished sign-in whose account was removed since has nothing to report.
        if finished is None or await fetch_account(account_id) is None:
            _FINISHED.pop(account_id, None)
            raise QrLoginError(_NO_QR_LOGIN)
        return QrLoginView(account_id=account_id, status="alive", account=finished)
    return await _view(state)


async def submit_qr_password(account_id: str, password: str) -> QrLoginView:
    """Answer the sign-in's 2FA prompt with the cloud password."""
    try:
        state = await _gateway_submit(account_id, password)
    except QrLoginGatewayError as exc:
        raise QrLoginError(exc.code) from exc
    return await _view(state)


async def cancel_qr_login(account_id: str) -> None:
    """Drop the account's QR sign-in (and its remembered outcome); no-op if none."""
    await _gateway_cancel(account_id)
    _FINISHED.pop(account_id, None)


async def _view(state: QrLoginState) -> QrLoginView:
    account = await _finish(state) if state.status == "alive" else None
    return QrLoginView(
        account_id=state.account_id,
        status=state.status,
        qr_image=state.qr_image,
        expires_at=state.expires_at,
        error_code=state.error_code,
        flood_wait_seconds=state.flood_wait_seconds,
        account=account,
    )


async def _finish(state: QrLoginState) -> AccountRead:
    """Persist the signed-in account once; drop it when Telebuba already had that user."""
    account_id = state.account_id
    lock = _FINISH_LOCKS.setdefault(account_id, asyncio.Lock())
    async with lock:
        done = _FINISHED.get(account_id)
        if done is not None:
            return done
        result = state.result
        if result is None:  # pragma: no cover - the gateway publishes alive with a result
            raise QrLoginError(_NO_QR_LOGIN)
        existing_id = await _existing_account_of(account_id, result.user_id)
        if existing_id is not None:
            await _drop_duplicate(account_id, existing_id, result.session_path)
            raise QrLoginError(_ACCOUNT_EXISTS)
        saved = await update_account_from_session_check(result)
        await start_account_inbox(account_id)
        forget_qr_login(account_id)
        await log_event("INFO", "qr_login_success", account_id=account_id)
        _FINISHED[account_id] = saved
        return saved


async def _existing_account_of(account_id: str, user_id: int | None) -> str | None:
    """The id of ANOTHER account that already is this Telegram user, if any."""
    if user_id is None:
        return None
    for other in (await list_accounts()).accounts:
        if other.user_id == user_id and other.account_id != account_id:
            return other.account_id
    return None


async def _drop_duplicate(account_id: str, existing_id: str, session_path: str) -> None:
    """Revoke the authorization the scan just created and delete the placeholder.

    The scanned account is already in Telebuba under another id, so this second
    authorization is surplus: logging it out removes it from the account's device
    list in Telegram, and the placeholder row goes with its ``.session`` file.
    """
    account = await fetch_account(account_id)
    try:
        await log_out_session(
            TelegramClientRequest(
                account_id=account_id,
                session_name=account.session_name if account else None,
                receive_updates=False,
            ),
            wipe_session=True,
        )
    except Exception as exc:  # noqa: BLE001 - the placeholder must go even if the revoke failed.
        await log_event(
            "WARNING",
            "qr_login_duplicate_logout_failed",
            account_id=account_id,
            extra={"error_type": type(exc).__name__},
        )
    await discard_imported_session(account_id, Path(f"{session_path}.session"))
    forget_qr_login(account_id)
    await log_event(
        "INFO",
        "qr_login_duplicate",
        account_id=account_id,
        extra={"existing_account_id": existing_id},
    )
