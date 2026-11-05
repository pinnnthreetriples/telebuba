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

from core.config import settings
from core.db import fetch_account_twofa_pair, set_account_twofa_password
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
    SetTwoFactorPassword,
    VerifyTwoFactorPassword,
)
from schemas.twofa import AccountTwoFactorCreated, AccountTwoFactorView
from services.accounts._result import (
    AccountActionError,
    AccountNotFoundError,
    raise_for_result,
    result_code,
)
from services.accounts._twofa_pending import (
    Settled,
    promote_pending,
    resolve_current,
    stage_pending,
    twofa_password_candidates,
)
from services.accounts.lifecycle import require_account

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult
    from schemas.telegram_actions_twofa import TwoFactorStatusResult
    from schemas.twofa import AccountTwoFactorUpdateRequest

__all__ = [
    "authorising_twofa_password",
    "authorising_twofa_password_unlocked",
    "read_account_twofa",
    "record_account_twofa",
    "remove_account_twofa",
    "set_account_twofa",
    "twofa_lock",
    "twofa_password_candidates",
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
        # Same deadline as the session probe (see ``core.telegram_client._session``).
        async with asyncio.timeout(settings.telegram.session_check_timeout_seconds):
            result = await execute_read(account_id, GetTwoFactorStatus())
    except TelegramReadError as exc:
        return None, exc.reason
    except TimeoutError:
        return None, "unavailable: TimeoutError"
    except TelegramAccountNotFoundError as exc:
        raise AccountNotFoundError(account_id) from exc
    return cast("TwoFactorStatusResult", result), None


async def _verify(account_id: str, password: str) -> bool | None:
    """Ask Telegram whether it accepts ``password``: yes, no, or unknown (nothing proven)."""
    result = await execute(account_id, VerifyTwoFactorPassword(password=password))
    if result.status == "ok":
        return True
    return False if result_code(result) == "twofa_current_password_invalid" else None


async def _settled_current(account_id: str, status: TwoFactorStatusResult | None) -> Settled:
    async def _ask(password: str) -> bool | None:
        return await _verify(account_id, password)

    return await resolve_current(account_id, status, _ask)


async def authorising_twofa_password_unlocked(account_id: str) -> str | None:
    """:func:`authorising_twofa_password` for a caller ALREADY holding :func:`twofa_lock`.

    The lock is not re-entrant, so the email flows (which take it around the whole
    read-then-write) call this one. With a candidate left unsettled the answer is the best
    guess; it only authorises, it never writes a column, so a wrong guess is a refusal
    from Telegram and nothing more.
    """
    _current, pending = await fetch_account_twofa_pair(account_id)
    if pending is None:
        return _current
    status, _error = await _live_status(account_id)
    return (await _settled_current(account_id, status)).password


async def authorising_twofa_password(account_id: str) -> str | None:
    """The stored password that authorises a write, after settling a pending candidate.

    For callers that do NOT hold :func:`twofa_lock` (the emulator and web sign-in): with
    nothing pending it is the stored current password and costs neither a Telegram call
    nor the lock; with a candidate pending it settles under the lock, because a settlement
    that clears a candidate must not interleave with a write that has just staged one.
    """
    current, pending = await fetch_account_twofa_pair(account_id)
    if pending is None:
        return current
    async with twofa_lock(account_id):
        return await authorising_twofa_password_unlocked(account_id)


async def read_account_twofa(account_id: str) -> AccountTwoFactorView:
    """The account's live 2FA state, plus whether this dashboard holds its password.

    ``has_stored_password`` is answered even when the live read failed: it is a DB
    fact, and it is what tells the operator whether a change or a removal can be
    authorised at all.
    """
    await require_account(account_id)
    stored = bool(await twofa_password_candidates(account_id))
    status, error = await _live_status(account_id)
    return AccountTwoFactorView(status=status, has_stored_password=stored, error=error)


async def set_account_twofa(
    account_id: str,
    request: AccountTwoFactorUpdateRequest,
) -> AccountTwoFactorCreated:
    """Set a new cloud password, or change the existing one, and return it once.

    On a CHANGE, a stored password is required: Telethon sends ``InputCheckPasswordEmpty``
    without one and Telegram answers a bare invalid-password error, so when Telegram
    reports a password and nothing is stored the refusal is ``twofa_password_not_stored``
    before any RPC. A live read that itself failed does NOT block the write.

    The candidate is staged in ``twofa_password_pending`` BEFORE the RPC, for a fresh set
    and a change alike, and a failed stage sends NOTHING (``twofa_pending_store_failed``):
    between "Telegram applied it" and any later persist there are awaits (``execute``
    awaits ``log_event`` itself), and dying in that window used to leave the password
    live on Telegram and only the old one (or none) in the row. An ANSWERED refusal
    proves Telegram did not apply it, so the previous pending value is restored. A
    confirmed write promotes pending to current. A pending candidate left by an earlier
    crash is settled first (``resolve_current``).

    A LOST ANSWER (``status="unavailable"`` with ``UNCONFIRMED_ERROR_TYPE``) means the
    request was on the wire, so Telegram may hold the password: it is returned like a
    success, flagged ``confirmed=False``, never discarded (the account would sit behind a
    password nobody saw). On a CHANGE over a live, known-good stored password BOTH stay
    (current + pending, every login path tries both) and ``previous_kept=True`` sends the
    operator to the phone — unless the live read says Telegram has no password, which
    makes the stored one stale; then the candidate is promoted. A fresh set promotes it.

    ``confirmed`` is also ``False`` when Telegram answered ``EMAIL_UNCONFIRMED`` and the
    gateway's confirming ``account.getPassword`` did not report a password.

    A promotion that fails still leaves the value in pending (usable for login) but the
    response says ``stored=False`` so the operator copies it.
    """
    await require_account(account_id)
    async with twofa_lock(account_id):
        password = request.password or secrets.token_urlsafe(_GENERATED_PASSWORD_BYTES)
        status, _error = await _live_status(account_id)
        settled = await _settled_current(account_id, status)
        if settled.unsettled:
            # A pending candidate Telegram has neither confirmed nor refused may be the
            # password in force: staging over it could leave Telegram holding a value in
            # neither column. Refuse; a later attempt settles it.
            code = "twofa_pending_unsettled"
            raise AccountActionError(code)
        # Settled, so this is the real stored current password, not a guess.
        current = settled.password
        if status is not None and status.has_password and current is None:
            code = "twofa_password_not_stored"
            raise AccountActionError(code)
        # The candidate is staged durably BEFORE the RPC, for a fresh set and a change
        # alike, and a failed stage sends nothing (see the docstring).
        previous_pending = (await fetch_account_twofa_pair(account_id))[1]
        if not await stage_pending(account_id, password):
            code = "twofa_pending_store_failed"
            raise AccountActionError(code)
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
            if result.status != "ok":
                # An answered refusal proves nothing was applied: put back whatever was
                # pending before. A failed restore is harmless (the next write or login
                # settles a stale candidate), so it is not an error here.
                await stage_pending(account_id, previous_pending)
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
        # Confirmed, or nothing live to lose: the candidate becomes the stored password.
        # Kept (an unknown outcome over a live one): BOTH stay, pending is the new one.
        stored = True if kept else await promote_pending(account_id, password)
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


async def record_account_twofa(account_id: str, password: str) -> bool:
    """Remember a cloud password the account ALREADY has, after Telegram accepts it.

    The verify action changes nothing on Telegram: a wrong password is
    ``twofa_current_password_invalid``, an account with no cloud password is
    ``twofa_not_enabled``, and only a verified value reaches the column. Returns
    whether it was stored (``False`` = the DB write failed, logged by
    ``_remember_password``). Under :func:`twofa_lock` like every other flow that
    writes the column. Booleans only in the log — the password never.

    An account with no proxy is refused (``twofa_proxy_missing``) BEFORE any Telegram
    call: the client connects directly then, which would leak the operator's own IP.
    """
    account = await require_account(account_id)
    if account.proxy_id is None:
        code = "twofa_proxy_missing"
        raise AccountActionError(code)
    async with twofa_lock(account_id):
        raise_for_result(await execute(account_id, VerifyTwoFactorPassword(password=password)))
        stored = await _remember_password(account_id, password)
        await log_event(
            "INFO",
            "account_twofa_recorded",
            account_id=account_id,
            extra={"stored": stored},
        )
        return stored


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
        status, _error = await _live_status(account_id)
        if forget_only:
            # Clear-only spends no RPC, so it never asks Telegram which candidate is in
            # force: that would burn checkPassword attempts on the very state this verb
            # exists to escape.
            current_stored, pending_stored = await fetch_account_twofa_pair(account_id)
            current = current_stored or pending_stored
        else:
            settled = await _settled_current(account_id, status)
            if settled.unsettled:
                code = "twofa_pending_unsettled"
                raise AccountActionError(code)
            current = settled.password
        if current is None:
            code = "twofa_password_not_stored"
            raise AccountActionError(code)
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
