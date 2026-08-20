"""The operator-in-the-loop recovery-email half of the 2FA domain.

Extracted sibling of ``services.accounts.twofa`` (which owns the live read and
the set / change / remove writes) purely for the 440-line file budget; the
dependency runs ONE way — this module imports ``read_account_twofa`` and the
per-account lock from there, and nothing there imports this. ``services.accounts``
re-exports all five entry points, so callers keep importing them from the package.

``execute`` and ``log_event`` are imported at module scope so tests can
monkeypatch ``services.accounts._twofa_email.execute`` — the reason
``services.accounts.privacy`` documents at its own module scope.

Secret discipline is the same rule one file over: the stored password, the
recovery address and the code Telegram mails all arrive in a request body and go
no further. Every log extra here carries the bounded ``mode`` and nothing else.

The event names are four separate literals rather than
``"account_twofa_email_" + mode``: ``tests/test_logevent_i18n_parity`` discovers
codes by reading literals out of the AST, and a concatenation or a dict lookup is
invisible to it — an untranslated event would then reach the operator as a raw
snake_case token.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.db import fetch_account_twofa_password
from core.logging import log_event
from core.telegram_client import UNCONFIRMED_ERROR_TYPE, execute
from schemas.telegram_actions_twofa import ManageTwoFactorEmail
from schemas.twofa import AccountTwoFactorEmailPending
from services.accounts._result import AccountActionError, raise_for_result
from services.accounts.lifecycle import require_account
from services.accounts.twofa import read_account_twofa, twofa_lock

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult
    from schemas.twofa import (
        AccountTwoFactorEmailConfirmRequest,
        AccountTwoFactorEmailRequest,
        AccountTwoFactorView,
    )

__all__ = [
    "cancel_account_twofa_email",
    "clear_account_twofa_email",
    "confirm_account_twofa_email",
    "resend_account_twofa_email",
    "set_account_twofa_email",
]


async def _run_email_action(account_id: str, action: ManageTwoFactorEmail) -> ActionResult:
    """Guard, dispatch and report one recovery-email mode; answer the raw result.

    The 404 guard runs here rather than in each caller so the five modes cannot
    drift apart on it. Callers own their own ``log_event`` literal — see the
    module docstring for why that is not centralised — and read what they need off
    the result, which is why it is returned whole rather than reduced to a length.
    """
    await require_account(account_id)
    result = await execute(account_id, action)
    raise_for_result(result)
    return result


async def set_account_twofa_email(
    account_id: str,
    request: AccountTwoFactorEmailRequest,
) -> AccountTwoFactorEmailPending:
    """Attach a recovery email, authorising with the password this dashboard stored.

    Nothing stored means the change cannot be authorised at all, so it is refused
    before any RPC with the same code the change and remove paths use — Telegram
    would otherwise answer a bare invalid-password error that explains nothing.

    ``pending`` is threaded from WHETHER Telegram answered ``EMAIL_UNCONFIRMED``, not
    derived from the code length. The bare form of that error carries ``code_length =
    0``, which the gateway reports as ``None`` because zero is not a length the card
    can size an input with — so a length-derived ``pending`` turned the one answer
    meaning "address accepted, code mailed" into ``{pending: false}``, which
    :class:`AccountTwoFactorEmailPending` documents as "already verified, nothing
    asked for". The card then dropped back to the empty attach form for an address
    that was pending. The length stays advisory.

    Serialised against the password writes under ``twofa_lock``, because this is the
    same read-then-authorised-write shape that registry exists for: reproduced with
    two ``updatePasswordSettings`` in flight for one account, authorised by two
    different passwords, and the loser told "wrong current password" about a password
    the dashboard itself had just replaced. There is no lock-ordering risk —
    ``_TWOFA_LOCKS`` never nests, ``read_account_twofa`` takes no lock,
    ``removing_client`` holds none across its yield, and ``_AUTH_LOCKS`` is taken only
    inside ``core.telegram_client._auth``.

    The 404 guard runs FIRST, before the stored-password one, even though
    ``_run_email_action`` repeats it: an unknown account has to answer 404 like its
    six siblings rather than 400 ``twofa_password_not_stored``.
    """
    async with twofa_lock(account_id):
        await require_account(account_id)
        current = await fetch_account_twofa_password(account_id)
        if current is None:
            code = "twofa_password_not_stored"
            raise AccountActionError(code)
        result = await _run_email_action(
            account_id,
            ManageTwoFactorEmail(mode="set", current_password=current, email=request.email),
        )
        pending = result.twofa_email_unconfirmed
        await log_event(
            "INFO",
            "account_twofa_email_set",
            account_id=account_id,
            extra={"mode": "set", "pending": pending},
        )
        return AccountTwoFactorEmailPending(
            pending=pending,
            code_length=result.twofa_email_code_length,
        )


async def confirm_account_twofa_email(
    account_id: str,
    request: AccountTwoFactorEmailConfirmRequest,
) -> AccountTwoFactorView:
    """Type the mailed code back; on success the pending email becomes the recovery one.

    Answers with the re-read live state so the card shows ``has_recovery`` without
    a second round trip — the shape ``remove_account_twofa`` already uses.

    A LOST ANSWER is not a failure here, for the reason ``set_account_twofa`` gives
    for its own version of it. ``account.confirmPasswordEmail`` was already on the
    wire, so the address may well be attached; answering 503 sends the operator back
    to a card that still holds the code, and the retry then gets
    ``twofa_email_code_invalid`` or ``twofa_email_hash_expired`` for an address that
    is already confirmed — an error that is a lie, and the code is single-use so no
    retry can ever succeed. So that ONE status re-reads the live state and reports
    success when ``has_recovery`` flipped. A read that failed, or one that still says
    no recovery email, keeps the 503: neither is proof of success.
    """
    await require_account(account_id)
    result = await execute(account_id, ManageTwoFactorEmail(mode="confirm", code=request.code))
    lost = result.status == "unavailable" and result.error_type == UNCONFIRMED_ERROR_TYPE
    if not lost:
        raise_for_result(result)
    view = await read_account_twofa(account_id)
    if lost and not (view.status is not None and view.status.has_recovery):
        raise_for_result(result)
    await log_event(
        "INFO",
        "account_twofa_email_confirmed",
        account_id=account_id,
        extra={"mode": "confirm"},
    )
    return view


async def resend_account_twofa_email(account_id: str) -> AccountTwoFactorEmailPending:
    """Mail the confirmation code again for an email that is still pending.

    ``code_length`` stays ``None``: ``account.resendPasswordEmail`` answers with a
    bare ``Bool`` and never repeats the length, and reporting the previous one
    would be a guess. ``pending`` is ``True`` by definition — Telegram refuses the
    call when there is nothing pending, and that refusal reaches the caller.
    """
    await _run_email_action(account_id, ManageTwoFactorEmail(mode="resend"))
    await log_event(
        "INFO",
        "account_twofa_email_resent",
        account_id=account_id,
        extra={"mode": "resend"},
    )
    return AccountTwoFactorEmailPending(pending=True)


async def clear_account_twofa_email(account_id: str) -> AccountTwoFactorView:
    """Detach a CONFIRMED recovery email — not the same call as cancelling a pending one.

    ``account.cancelPasswordEmail`` only abandons a verification still in flight. A
    confirmed address comes off with ``updatePasswordSettings`` and an empty
    ``email``, which needs the stored password to authorise it — so this refuses up
    front with the same code the other authorised writes use, after the 404 guard and
    under the lock for the two reasons ``set_account_twofa_email`` documents.
    """
    async with twofa_lock(account_id):
        await require_account(account_id)
        current = await fetch_account_twofa_password(account_id)
        if current is None:
            code = "twofa_password_not_stored"
            raise AccountActionError(code)
        await _run_email_action(
            account_id,
            ManageTwoFactorEmail(mode="clear", current_password=current),
        )
        await log_event(
            "INFO",
            "account_twofa_email_cleared",
            account_id=account_id,
            extra={"mode": "clear"},
        )
    # Outside the lock: the read takes none, and holding one across it would be the
    # first nesting in this registry.
    return await read_account_twofa(account_id)


async def cancel_account_twofa_email(account_id: str) -> AccountTwoFactorView:
    """Abandon a pending recovery email. The cloud password is untouched.

    Nothing is cleared locally because nothing was stored locally: the address
    lives only on Telegram, which is why this feature added no column.
    """
    await _run_email_action(account_id, ManageTwoFactorEmail(mode="cancel"))
    await log_event(
        "INFO",
        "account_twofa_email_cancelled",
        account_id=account_id,
        extra={"mode": "cancel"},
    )
    return await read_account_twofa(account_id)
