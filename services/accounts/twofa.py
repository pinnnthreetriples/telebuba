"""Cloud-password (2FA) reads/writes for the accounts domain.

Why this exists: an account with no cloud password is one phone number and one
login code away from being taken over, and Telegram offers no way to read the
password back — only whether one is set. So this module owns the live read the
card renders and the set / change / remove writes.

The recovery-email half lives in the extracted sibling
``services.accounts._twofa_email`` (440-line file budget); it imports
:func:`read_account_twofa` and :func:`twofa_lock` from here, never the reverse.

``execute`` / ``execute_read`` are imported at module scope so tests can
monkeypatch ``services.accounts.twofa.execute`` (same for ``execute_read`` and
the two persistence functions) — the reason ``services.accounts.privacy``
documents at its own module scope.

Secret discipline, the rule that outranks everything else here: the password
reaches exactly one response, :class:`AccountTwoFactorCreated`, and nothing else.
Not a ``log_event`` name, not an ``extra`` value, not an error message. Every log
extra below carries booleans only, and ``tests/services/accounts/test_twofa*.py``
assert that the password never turns up in a view or a log.
"""

from __future__ import annotations

import asyncio
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
from schemas.telegram_actions_twofa import GetTwoFactorStatus, SetTwoFactorPassword
from schemas.twofa import AccountTwoFactorCreated, AccountTwoFactorView
from services.accounts._result import (
    AccountActionError,
    AccountNotFoundError,
    raise_for_result,
)
from services.accounts.lifecycle import require_account

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult
    from schemas.telegram_actions_twofa import TwoFactorStatusResult
    from schemas.twofa import AccountTwoFactorUpdateRequest

__all__ = [
    "read_account_twofa",
    "remove_account_twofa",
    "set_account_twofa",
    "twofa_lock",
]

# ``secrets.token_urlsafe(16)`` → 22 URL-safe characters over 128 bits of
# entropy. Generation is policy, so it lives here and not in ``core/``: the
# gateway sets whatever password it is handed.
_GENERATED_PASSWORD_BYTES = 16

# One lock per account, created lazily and never freed — the shape
# ``core.telegram_client._auth._AUTH_LOCKS`` uses. It covers the two flows that READ
# the stored password and then WRITE it back: two tabs would otherwise both read the
# same ``current``, both send it, and the loser's persist could clobber the winner's.
# ``tests/services/accounts/conftest.py`` clears it per test — a lock binds to the
# loop that first awaited it. ``services.accounts.lifecycle.remove_account`` drops the
# key, like the two other per-account registries there.
#
# ponytail: known residual, deliberately not fixed here. This lock serialises the
# dashboard against ITSELF; it cannot stop ``core.telegram_client.evict_client`` from
# disconnecting a pooled client out from under an in-flight 2FA write. The realistic
# trigger is proxy rotation — ``add_proxy`` / ``remove_proxy`` evict every account on a
# proxy, from another domain entirely — and the real fix is a borrow refcount in the
# pool, a cross-cutting change to something every domain uses, which is not this PR.
# What makes the residual survivable is that every outcome of a killed write is now
# recoverable: a fresh set persists BEFORE the RPC, and a stale stored password is
# droppable through the remove path's stale branch.
#
# These writes deliberately do NOT join ``core.telegram_client._auth._AUTH_LOCKS``.
# That lock guards a measured two-``SQLiteSession``-handles-on-one-file hazard between
# flows that build their own clients; 2FA borrows from the pool and never opens a
# second handle. What it needs is the converse guarantee — "do not disconnect the
# client I am holding" — which nothing provides today.
_TWOFA_LOCKS: dict[str, asyncio.Lock] = {}


def twofa_lock(account_id: str) -> asyncio.Lock:
    """Serialise the authorised writes for one account.

    Public because ``_twofa_email`` takes the same lock: its ``set`` and ``clear``
    modes read the stored password and then send an SRP-authorised
    ``updatePasswordSettings`` with it, which is the read-then-write shape this
    registry exists for.
    """
    lock = _TWOFA_LOCKS.get(account_id)
    if lock is None:
        lock = asyncio.Lock()
        _TWOFA_LOCKS[account_id] = lock
    return lock


async def _live_status(account_id: str) -> tuple[TwoFactorStatusResult | None, str | None]:
    """``(status, error reason)`` — a refused read is data here, not an exception.

    Error-envelope idiom (see ``AccountPrivacyView``): the card must still render
    when Telegram refuses. An unknown account is a genuine 404 and does raise —
    including when the row disappears between a caller's guard and this read,
    which the gateway reports with its own error type; without translating it the
    route would answer 500.

    ponytail: a refused read logs NOTHING, by design — the envelope is the report and
    ``execute_read`` does not log. The cost is that "why did the card show an error at
    14:32" cannot be answered from the log afterwards. Recorded, not fixed: a log line
    per refused read would fire on every poll of an unreachable account.
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

    On a FRESH SET the persist happens BEFORE the RPC, and that ordering is the only
    thing standing between a process death and an unrecoverable account. Between
    "Telegram applied it" and a post-RPC persist there are several awaits —
    ``execute`` itself awaits ``log_event``, a threaded SQLite insert plus an SSE
    fan-out — and dying in that window leaves the password live on Telegram, nothing
    in the row and no log line: the plaintext existed only in a response nobody
    received. Writing first inverts it into a recoverable state, stored-but-never-
    applied, which reads as ``has_stored_password=True`` with ``has_password=False``
    and is exactly what the stale branch below and the card's "forget the stale
    password" row are for. An ANSWERED refusal rolls that write back, because the
    answer is proof Telegram did not apply it; only the two ambiguous outcomes (a
    lost answer, a killed process) leave it in place.

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

    That persist is asymmetric, and the asymmetry is the point: it happens only when
    there was NOTHING LIVE to lose. On a CHANGE the stored value is a credential
    Telegram is known to accept, so overwriting it with one that may never have been
    applied would trade a recoverable ambiguity for exactly the unrecoverable loss
    above. A change therefore keeps the old value, still returns the new one (Telegram
    may hold it) and says ``previous_kept=True``, so the card can send the operator to
    the phone — UNLESS the live read says Telegram has no password at all, in which
    case the stored value is stale by definition, cannot be "the previous one in
    force", and keeping it would persist nothing at all.

    ``confirmed`` can also be ``False`` when Telegram answered ``EMAIL_UNCONFIRMED``,
    but that is now the gateway's call rather than an assumption: it issues one
    confirming ``account.getPassword`` and reports the flag only when that read did
    not come back saying a password is set. See ``_twofa._email_unconfirmed_result``
    for why the previous round's TDLib citation was fabricated.
    """
    await require_account(account_id)
    async with twofa_lock(account_id):
        password = request.password or secrets.token_urlsafe(_GENERATED_PASSWORD_BYTES)
        current = await fetch_account_twofa_password(account_id)
        status, _error = await _live_status(account_id)
        if status is not None and status.has_password and current is None:
            code = "twofa_password_not_stored"
            raise AccountActionError(code)
        # FRESH SET ONLY: nothing live to lose, so the candidate is written first and
        # the silent-loss window closes (see the docstring).
        fresh = current is None
        stored = fresh and await _remember_password(
            account_id,
            password,
            has_hint=bool(request.hint),
        )
        result = await execute(
            account_id,
            # ``hint=None`` means KEEP whatever Telegram shows; the gateway resolves it
            # against its own fresh read, so an omitted hint cannot erase the live one.
            SetTwoFactorPassword(
                current_password=current,
                new_password=password,
                hint=request.hint,
            ),
        )
        lost = result.status == "unavailable" and result.error_type == UNCONFIRMED_ERROR_TYPE
        if not lost:
            if result.status != "ok" and stored and not await _remember_password(account_id, None):
                # The ROLLBACK itself failed, and that is not a plain refusal any
                # more: the column keeps a password Telegram provably rejected, so
                # ``has_stored_password`` stays ``True``, the not-stored guard can
                # never fire again and every later verb authorises itself with a
                # value Telegram will reject. Its own code, so the operator is told
                # to forget the stored password rather than just "try again".
                code = "twofa_rollback_failed"
                raise AccountActionError(code)
            raise_for_result(result)
        # A stored password the live read contradicts is stale BY DEFINITION, so it is
        # not the "known to work" credential this branch exists to protect — keeping it
        # would discard the password Telegram may now require and persist nothing at
        # all, which is the terminal state the docstring describes.
        stale_stored = status is not None and not status.has_password
        confirmed = not lost and not result.twofa_email_unconfirmed
        kept = not confirmed and current is not None and not stale_stored
        # UNKNOWN, not ``True``: with no live read there is no evidence Telegram holds
        # a password at all, so "the previous one or this one is in force" would be an
        # assertion drawn from a read that answered nothing.
        previous_kept = None if kept and status is None else kept
        if not fresh and not kept:
            stored = await _remember_password(
                account_id,
                password,
                has_hint=bool(request.hint),
            )
        # ponytail: INFO/success even on the lost-answer branch, where ``confirmed`` is
        # ``False``. Correct — Telegram may well hold the password, and the row carries
        # the flag — but an operator skimming the log sees a success row for an outcome
        # that needs their attention. Recorded rather than promoted to WARNING, because
        # the event name is what the i18n table and the log filters key off.
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
            # What the GATEWAY WROTE. It resolves an omitted hint against its own fresh
            # ``account.getPassword``, so reporting this layer's separate read could name
            # a hint that never reached the wire. ``None`` only when the answer was lost,
            # and then the request field is the best this layer has.
            hint=_reported_hint(result, request, status),
            stored=stored,
            confirmed=confirmed,
            previous_kept=previous_kept,
        )


def _reported_hint(
    result: ActionResult,
    request: AccountTwoFactorUpdateRequest,
    status: TwoFactorStatusResult | None,
) -> str | None:
    """The hint to REPORT: what the gateway wrote, or the best guess if it never said.

    The gateway is the only layer that knows the written value — it resolves
    ``hint=None`` ("keep") against its own fresh ``account.getPassword`` — so its
    answer wins outright. It is absent only when the answer was lost, in which case
    the request field, then this layer's own read, then "unknown" is the ladder.
    """
    if result.twofa_hint is not None:
        return result.twofa_hint
    if request.hint is not None:
        return request.hint
    return status.hint if status else None


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

    A write that touched NO ROW is reported exactly like a failed one. The statement
    is an ``UPDATE ... WHERE``, so an account deleted between the guard and here
    updates nothing and raises nothing — and claiming ``stored=True`` for a password
    that exists nowhere is the one lie this response must not tell.
    """
    try:
        stored = await set_account_twofa_password(account_id, password)
    except Exception:  # noqa: BLE001 - the RPC already succeeded; see the docstring
        stored = False
    if not stored:
        await log_event(
            "ERROR",
            "account_twofa_store_failed",
            account_id=account_id,
            extra={"has_hint": has_hint, "clearing": password is None},
        )
    return stored


async def remove_account_twofa(
    account_id: str,
    *,
    forget_only: bool = False,
) -> AccountTwoFactorView:
    """Turn 2FA off, authorising with the password this dashboard stored.

    Nothing stored means nothing to authorise with, and it must refuse rather than
    try: Telethon drops a current password when the account has no 2FA, so a blind
    "remove" would have sent a request that changes nothing and reported it as done.
    The ``twofa_password_not_stored`` refusal names the real situation — the password
    was set outside this dashboard, so it has to be removed there too.

    The column is cleared only after Telegram confirmed: a stored password whose
    2FA is gone guards nothing, but clearing it first would destroy the one copy
    that could authorise a retry.

    ``forget_only`` is the CLEAR-ONLY verb, and it spends no RPC. It is what makes
    every "the column holds a password Telegram does not accept" state recoverable,
    and there are several: a rollback whose UPDATE failed, a process death between a
    fresh set's pre-write and its RPC, a lost answer over a live read that itself
    failed. All of them are otherwise terminal — ``has_stored_password`` stays
    ``True`` so the not-stored guard can never fire, every verb authorises itself
    with the bogus value, and the ``stale`` branch below cannot help because the live
    read says ``has_password=True``. Only the operator can distinguish "this stored
    value is worthless" from "this is my working password", so it is an explicit
    request rather than an inference.

    The ``stale`` branch is the same clear, taken automatically: the live read
    already says 2FA is OFF, so the stored value is stale by definition and there is
    nothing left on Telegram's side to remove.

    Serialised against a concurrent set/change for the reason ``_TWOFA_LOCKS`` gives:
    this flow also reads the stored password and then writes the column back. The 404
    guard runs BEFORE the lock, or a POST naming an account that does not exist would
    mint a lock nothing ever removes.
    """
    await require_account(account_id)
    async with twofa_lock(account_id):
        current = await fetch_account_twofa_password(account_id)
        if current is None:
            code = "twofa_password_not_stored"
            raise AccountActionError(code)
        status, _error = await _live_status(account_id)
        stale = status is not None and not status.has_password
        if not stale and not forget_only:
            remove = SetTwoFactorPassword(current_password=current)
            raise_for_result(await execute(account_id, remove))
        await _remember_password(account_id, None)
        await log_event(
            "INFO",
            "account_twofa_removed",
            account_id=account_id,
            extra={"stale": stale, "forget_only": forget_only},
        )
        return await read_account_twofa(account_id)
