"""Lock registry and hint reporting split out of ``services.accounts.twofa`` (file budget).

Pure helpers with no Telegram I/O, so no test seam lives here; ``twofa`` re-exports
:func:`twofa_lock` and ``_TWOFA_LOCKS`` and imports from here, never the reverse.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import TYPE_CHECKING

from core.db import fetch_account_twofa_pair
from services.accounts._result import AccountActionError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.accounts import AccountRead
    from schemas.telegram_actions import ActionResult
    from schemas.telegram_actions_twofa import TwoFactorStatusResult
    from schemas.twofa import AccountTwoFactorUpdateRequest

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


def reported_hint(
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


def set_outcome(
    status: TwoFactorStatusResult | None,
    current: str | None,
    *,
    lost: bool,
    email_unconfirmed: bool,
) -> tuple[bool, bool, bool | None]:
    """``(confirmed, kept, previous_kept)`` for a set/change attempt."""
    # A stored password the live read contradicts is stale BY DEFINITION, so it is
    # not the "known to work" credential this branch exists to protect — keeping it
    # would discard the password Telegram may now require and persist nothing at
    # all, which is the terminal state the docstring describes.
    stale_stored = status is not None and not status.has_password
    confirmed = not lost and not email_unconfirmed
    kept = not confirmed and current is not None and not stale_stored
    # UNKNOWN, not ``True``: with no live read there is no evidence Telegram holds
    # a password at all, so "the previous one or this one is in force" would be an
    # assertion drawn from a read that answered nothing.
    previous_kept = None if kept and status is None else kept
    return confirmed, kept, previous_kept


# ``secrets.token_urlsafe(16)`` → 22 URL-safe characters over 128 bits of
# entropy. Generation is policy, so it lives here and not in ``core/``: the
# gateway sets whatever password it is handed.
_GENERATED_PASSWORD_BYTES = 16


def generated_password(requested: str | None) -> str:
    """The caller's password, or a freshly generated one."""
    return requested or secrets.token_urlsafe(_GENERATED_PASSWORD_BYTES)


async def locked_authorising(
    account_id: str, unlocked: Callable[[str], Awaitable[str | None]]
) -> str | None:
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
        return await unlocked(account_id)


def require_proxy(account: AccountRead) -> None:
    """Refuse (``twofa_proxy_missing``) an account with no proxy before any Telegram call."""
    if account.proxy_id is None:
        code = "twofa_proxy_missing"
        raise AccountActionError(code)
