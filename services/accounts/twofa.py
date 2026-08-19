"""Cloud-password (2FA) reads/writes for the accounts domain.

Why this exists: an account with no cloud password is one phone number and one
login code away from being taken over, and Telegram offers no way to read the
password back — only whether one is set. So this module owns both halves: the
live read the card renders, the set / change / remove writes, and the
operator-in-the-loop recovery-email flow (attach, confirm, resend, cancel).

``execute`` / ``execute_read`` are imported at module scope so tests can
monkeypatch ``services.accounts.twofa.execute`` (same for ``execute_read`` and
the two persistence functions) — the reason ``services.accounts.privacy``
documents at its own module scope.

Secret discipline, the rule that outranks everything else here: the password
reaches exactly one response, :class:`AccountTwoFactorCreated`, and nothing else.
Not a ``log_event`` name, not an ``extra`` value, not an error message. The
recovery email address and the confirmation code Telegram mails are the same:
they arrive in a request body and go no further. Every log extra below carries
booleans and the bounded ``mode`` only, and
``tests/services/accounts/test_twofa*.py`` assert that none of the three ever
turns up in a view or a log.

The email event names are four separate literals rather than
``"account_twofa_email_" + mode``: ``tests/test_logevent_i18n_parity`` discovers
codes by reading literals out of the AST, and a concatenation or a dict lookup is
invisible to it — an untranslated event would then reach the operator as a raw
snake_case token.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, cast

from core.db import fetch_account_twofa_password, set_account_twofa_password
from core.logging import log_event
from core.telegram_client import (
    UNCONFIRMED_ERROR_TYPE,
    TelegramAccountNotFoundError,
    TelegramReadError,
    execute,
    execute_read,
)
from schemas.telegram_actions_twofa import (
    GetTwoFactorStatus,
    ManageTwoFactorEmail,
    SetTwoFactorPassword,
)
from schemas.twofa import (
    AccountTwoFactorCreated,
    AccountTwoFactorEmailPending,
    AccountTwoFactorView,
)
from services.accounts._result import (
    AccountActionError,
    AccountNotFoundError,
    raise_for_result,
)
from services.accounts.lifecycle import require_account

if TYPE_CHECKING:
    from schemas.telegram_actions_twofa import TwoFactorStatusResult
    from schemas.twofa import (
        AccountTwoFactorEmailConfirmRequest,
        AccountTwoFactorEmailRequest,
        AccountTwoFactorUpdateRequest,
    )

__all__ = [
    "cancel_account_twofa_email",
    "clear_account_twofa_email",
    "confirm_account_twofa_email",
    "read_account_twofa",
    "remove_account_twofa",
    "resend_account_twofa_email",
    "set_account_twofa",
    "set_account_twofa_email",
]

# ``secrets.token_urlsafe(16)`` → 22 URL-safe characters over 128 bits of
# entropy. Generation is policy, so it lives here and not in ``core/``: the
# gateway sets whatever password it is handed.
_GENERATED_PASSWORD_BYTES = 16


async def _live_status(account_id: str) -> tuple[TwoFactorStatusResult | None, str | None]:
    """``(status, error reason)`` — a refused read is data here, not an exception.

    Error-envelope idiom (see ``AccountPrivacyView``): the card must still render
    when Telegram refuses. An unknown account is a genuine 404 and does raise —
    including when the row disappears between a caller's guard and this read,
    which the gateway reports with its own error type; without translating it the
    route would answer 500.
    """
    try:
        result = await execute_read(account_id, GetTwoFactorStatus())
    except TelegramReadError as exc:
        return None, exc.reason
    except TelegramAccountNotFoundError as exc:
        raise AccountNotFoundError(account_id) from exc
    return cast("TwoFactorStatusResult", result), None


async def read_account_twofa(account_id: str) -> AccountTwoFactorView:
    """The account's live 2FA state, plus whether this dashboard holds its password.

    ``has_stored_password`` is answered even when the live read failed: it is a DB
    fact, and it is what tells the operator whether a change or a removal can be
    authorised at all.
    """
    await require_account(account_id)
    stored = bool(await fetch_account_twofa_password(account_id))
    status, error = await _live_status(account_id)
    return AccountTwoFactorView(status=status, has_stored_password=stored, error=error)


async def set_account_twofa(
    account_id: str,
    request: AccountTwoFactorUpdateRequest,
) -> AccountTwoFactorCreated:
    """Set a new cloud password, or change the existing one, and return it once.

    The precondition on a CHANGE is the part worth reading. Telethon sends
    ``InputCheckPasswordEmpty`` when it has no current password to check, and
    Telegram answers that with a bare invalid-password error — accurate but
    useless, because the real problem is that this dashboard never held the
    password (the account was set up elsewhere, or the column was cleared). So
    when Telegram reports a password is already set and nothing is stored, the
    refusal is ``twofa_password_not_stored`` before any RPC is spent. A live read
    that itself failed does NOT block the write: ``edit_2fa`` rejects a wrong or
    missing current password by itself, so this check is a better message, not the
    safety net.

    A persistence failure does NOT fail the request. Telegram has already accepted
    the password by then, so this response is the operator's only copy; dropping it
    would strand the account behind a password nobody holds. The response says
    ``stored=False`` instead, and the failure is logged.

    A LOST ANSWER is handled the same way, for a stronger version of the same
    reason. ``status="unavailable"`` with ``error_type == UNCONFIRMED_ERROR_TYPE``
    means the request was already on the wire, so Telegram may have applied this
    password. Letting that reach ``raise_for_result`` would answer 503 and discard
    the value — and if Telegram DID apply it, the account is then behind a password
    no human ever saw, which nothing can undo: the retry reads ``has_password=True``
    with nothing stored and refuses ``twofa_password_not_stored`` forever, so no set,
    no change, no remove, and ``submit_phone_code`` can never complete after a
    session reset. So that one status persists and returns the password like a
    success, flagged ``confirmed=False``. Every other non-ok status still raises.
    """
    await require_account(account_id)
    password = request.password or secrets.token_urlsafe(_GENERATED_PASSWORD_BYTES)
    current = await fetch_account_twofa_password(account_id)
    status, _error = await _live_status(account_id)
    if status is not None and status.has_password and current is None:
        code = "twofa_password_not_stored"
        raise AccountActionError(code)
    result = await execute(
        account_id,
        SetTwoFactorPassword(
            current_password=current,
            new_password=password,
            hint=request.hint or "",
        ),
    )
    confirmed = not (result.status == "unavailable" and result.error_type == UNCONFIRMED_ERROR_TYPE)
    if confirmed:
        raise_for_result(result)
    stored = await _remember_password(account_id, password, has_hint=bool(request.hint))
    await log_event(
        "INFO",
        "account_twofa_set",
        account_id=account_id,
        extra={
            "has_hint": bool(request.hint),
            "generated": request.password is None,
            "changed": current is not None,
            "confirmed": confirmed,
        },
    )
    return AccountTwoFactorCreated(
        password=password,
        hint=request.hint,
        stored=stored,
        confirmed=confirmed,
    )


async def _remember_password(
    account_id: str,
    password: str | None,
    *,
    has_hint: bool = False,
) -> bool:
    """Store the accepted password (``None`` clears it); report failure, never raise.

    Nothing about the failed attempt reaches the log beyond two booleans: the value
    we could not write IS the secret, so neither it nor anything derived from the
    write (SQLAlchemy renders bound parameters into its messages) may travel. That
    is also why this does not fall back to the stdlib-logger sink the rest of the
    repo uses for third-party exception text.

    The removal path routes its clear through here too. Telegram has already turned
    2FA off by then, so a locked database must not answer 500 for a removal that
    succeeded: that would leave the plaintext in SQLite guarding nothing while
    telling the operator the removal failed.
    """
    try:
        await set_account_twofa_password(account_id, password)
    except Exception:  # noqa: BLE001 - the RPC already succeeded; see the docstring
        await log_event(
            "ERROR",
            "account_twofa_store_failed",
            account_id=account_id,
            extra={"has_hint": has_hint, "clearing": password is None},
        )
        return False
    return True


async def remove_account_twofa(account_id: str) -> AccountTwoFactorView:
    """Turn 2FA off, authorising with the password this dashboard stored.

    Nothing stored means nothing to authorise with, and it must refuse rather than
    try: Telethon drops a current password when the account has no 2FA and then
    returns ``False`` from a call it never made, so a blind "remove" would have
    reported success while changing nothing. The ``twofa_password_not_stored``
    refusal names the real situation — the password was set outside this
    dashboard, so it has to be removed there too.

    The column is cleared only after Telegram confirmed: a stored password whose
    2FA is gone guards nothing, but clearing it first would destroy the one copy
    that could authorise a retry.

    Unless the live read already says 2FA is OFF, in which case the stored value is
    stale by definition and this is the only affordance that can drop it. That state
    is reachable two ways — the operator removed the password from their phone, or an
    earlier removal's post-RPC clear failed — and without this branch it is terminal:
    ``has_stored_password`` stays ``True``, so the card keeps offering change /
    remove / attach-email and all three fail (a remove hits Telethon's ``if not
    pwd.has_password and current_password: current_password = None``, returns
    ``False``, and surfaces as ``twofa_not_changed``). No RPC is spent: there is
    nothing left on Telegram's side to remove.
    """
    await require_account(account_id)
    current = await fetch_account_twofa_password(account_id)
    if current is None:
        code = "twofa_password_not_stored"
        raise AccountActionError(code)
    status, _error = await _live_status(account_id)
    stale = status is not None and not status.has_password
    if not stale:
        raise_for_result(await execute(account_id, SetTwoFactorPassword(current_password=current)))
    await _remember_password(account_id, None)
    await log_event("INFO", "account_twofa_removed", account_id=account_id, extra={"stale": stale})
    return await read_account_twofa(account_id)


async def _run_email_action(account_id: str, action: ManageTwoFactorEmail) -> int | None:
    """Guard, dispatch and report one recovery-email mode; answer the code length.

    The 404 guard runs here rather than in each caller so the four modes cannot
    drift apart on it. Callers own their own ``log_event`` literal — see the
    module docstring for why that is not centralised.
    """
    await require_account(account_id)
    result = await execute(account_id, action)
    raise_for_result(result)
    return result.twofa_email_code_length


async def set_account_twofa_email(
    account_id: str,
    request: AccountTwoFactorEmailRequest,
) -> AccountTwoFactorEmailPending:
    """Attach a recovery email, authorising with the password this dashboard stored.

    Nothing stored means the change cannot be authorised at all, so it is refused
    before any RPC with the same code the change and remove paths use — Telegram
    would otherwise answer a bare invalid-password error that explains nothing.

    ``pending`` is derived from the code length rather than threaded separately:
    the length exists only inside ``EMAIL_UNCONFIRMED_<N>``, and that error IS
    Telegram saying it has mailed a code. Its absence therefore means Telegram
    accepted the address as already verified and asked for nothing.

    The 404 guard runs FIRST, before the stored-password one, even though
    ``_run_email_action`` repeats it: an unknown account has to answer 404 like its
    six siblings rather than 400 ``twofa_password_not_stored``.
    """
    await require_account(account_id)
    current = await fetch_account_twofa_password(account_id)
    if current is None:
        code = "twofa_password_not_stored"
        raise AccountActionError(code)
    code_length = await _run_email_action(
        account_id,
        ManageTwoFactorEmail(mode="set", current_password=current, email=request.email),
    )
    await log_event(
        "INFO",
        "account_twofa_email_set",
        account_id=account_id,
        extra={"mode": "set", "pending": code_length is not None},
    )
    return AccountTwoFactorEmailPending(pending=code_length is not None, code_length=code_length)


async def confirm_account_twofa_email(
    account_id: str,
    request: AccountTwoFactorEmailConfirmRequest,
) -> AccountTwoFactorView:
    """Type the mailed code back; on success the pending email becomes the recovery one.

    Answers with the re-read live state so the card shows ``has_recovery`` without
    a second round trip — the shape ``remove_account_twofa`` already uses.
    """
    await _run_email_action(account_id, ManageTwoFactorEmail(mode="confirm", code=request.code))
    await log_event(
        "INFO",
        "account_twofa_email_confirmed",
        account_id=account_id,
        extra={"mode": "confirm"},
    )
    return await read_account_twofa(account_id)


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
    front with the same code the other authorised writes use, after the 404 guard for
    the reason ``set_account_twofa_email`` documents.
    """
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
