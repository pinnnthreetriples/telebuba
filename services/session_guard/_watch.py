"""The watch: re-read every alive account's sessions, a few hours apart, one account a pass.

A foreign session can appear any day after the first audit, and a session that is ended by
someone else is only noticed when an action fails. The watch re-runs the audit of each alive
account behind a proxy every few hours, so a NEW foreign session is journalled and sent to
the operator (``session_audit_foreign_found``) soon after it shows up, and a later death can
be bracketed by the last audit before it.

It is a read of one list, not an action, but it is still a Telegram connection through the
account's proxy, so it is paced like the survival probes (``services.survival``): one account
per pass, no two accounts behind the same proxy within a spacing, and an interval per account
drawn afresh from a range (a clipped log-normal, drawn here: core holds no randomness), so a
fleet imported together never reads in step. An account another feature holds is skipped
until it is free. Due times and proxy stamps live in memory, which is safe with one uvicorn
worker; after a restart the saved audit time and a fresh draw decide, and the first pass waits.

An account that has a stored passkey is asked whether Telegram still lists it
(``check_account_passkey``, read-only): a passkey that is gone means somebody else was in the
account's security settings.

The same pass also asks the key vault whether the account's passkey key is reachable and
still there (``check_passkey_vault``): a sealed vault, a revoked token or a deleted key
is journalled (``passkey_vault_unhealthy``) before the operator needs the passkey, not
when a login fails.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from core.repositories._accounts_passkey import fetch_account_passkey
from core.repositories.accounts import list_accounts
from core.repositories.session_audits import list_session_audit_times
from schemas.accounts import AccountCheckRequest
from services import pacing
from services._account_owner import is_lent, owner_of
from services.accounts import (
    AccountActionError,
    AccountNotFoundError,
    audit_account_sessions,
    check_account_passkey,
    check_account_session,
    check_passkey_vault,
)

if TYPE_CHECKING:
    from schemas.accounts import AccountRead

# Seam for tests; the intervals are the only thing drawn.
_rng = random.Random()  # nosec B311  # noqa: S311 - pacing jitter, not security

_SECONDS_PER_HOUR = 3600
# The audit's refusals that mean the session itself is gone: the death goes through the
# session check, which records the status and journals it with the last audit before it.
_DEAD_CODES = frozenset({"session_dead", "account_deactivated", "account_frozen"})


@dataclass
class _State:
    started: float = field(default_factory=time.monotonic)
    # When this process will next read each account, and when it last read behind each proxy.
    due: dict[str, datetime] = field(default_factory=dict)
    asked_by_proxy: dict[str, datetime] = field(default_factory=dict)


_state = _State()


def _interval() -> timedelta:
    """One account's wait until its next read: a fresh draw from the configured range."""
    limits = settings.session_audit
    return timedelta(
        seconds=pacing.human_delay(
            limits.watch_min_hours * _SECONDS_PER_HOUR,
            limits.watch_max_hours * _SECONDS_PER_HOUR,
            rng=_rng,
            mu=limits.delay_lognorm_mu,
            sigma=limits.delay_lognorm_sigma,
        )
    )


def _free(account: AccountRead) -> bool:
    """An alive account behind a proxy that no other feature holds right now."""
    return (
        account.status == "alive"
        and account.proxy_id is not None
        and owner_of(account.account_id) is None
        and not is_lent(account.account_id)
    )


def _due_at(account: AccountRead, audited: dict[str, str], now: datetime) -> datetime:
    """When this process should next read ``account``: its remembered time, else a draw.

    An account never audited is due now. One audited before this process started is due one
    drawn interval after that audit, so a restart neither re-reads the fleet at once nor
    forgets what the last read was.
    """
    known = _state.due.get(account.account_id)
    if known is not None:
        return known
    last = audited.get(account.account_id)
    due = now if last is None else datetime.fromisoformat(last) + _interval()
    _state.due[account.account_id] = due
    return due


def _pick(
    accounts: list[AccountRead], audited: dict[str, str], now: datetime
) -> AccountRead | None:
    """The most overdue free account whose proxy was not read within the spacing."""
    spacing = timedelta(seconds=settings.session_audit.watch_proxy_spacing_seconds)
    ready: list[tuple[datetime, AccountRead]] = []
    for account in accounts:
        if not _free(account):
            continue
        due = _due_at(account, audited, now)
        last_on_proxy = _state.asked_by_proxy.get(account.proxy_id or "")
        if due <= now and (last_on_proxy is None or now - last_on_proxy >= spacing):
            ready.append((due, account))
    return min(ready, key=lambda item: item[0])[1] if ready else None


async def _check_passkey(account_id: str) -> None:
    """Journal the passkey Telegram no longer lists; anything else is not news."""
    if await fetch_account_passkey(account_id) is None:
        return
    try:
        check = await check_account_passkey(account_id)
    except (AccountActionError, AccountNotFoundError):
        return
    if check.telegram_state == "missing":
        await log_event("WARNING", "session_watch_passkey_missing", account_id=account_id)


async def _read(account_id: str) -> bool:
    """Audit one account; whether it answered. A dead session goes through the check."""
    try:
        await audit_account_sessions(account_id)
    except AccountActionError as exc:
        if exc.code in _DEAD_CODES:
            await check_account_session(AccountCheckRequest(account_id=account_id))
        return False
    except AccountNotFoundError:
        return False
    return True


async def run_watch_pass(*, now: datetime | None = None) -> str | None:
    """Audit the most overdue account, if one is due; its id, or ``None``."""
    limits = settings.session_audit
    if not limits.watch_enabled:
        return None
    if time.monotonic() - _state.started < limits.watch_startup_delay_seconds:
        return None
    moment = now or datetime.now(UTC)
    audited = await list_session_audit_times()
    account = _pick((await list_accounts()).accounts, audited, moment)
    if account is None:
        return None
    _state.asked_by_proxy[account.proxy_id or ""] = moment
    answered = await _read(account.account_id)
    # A refused read is tried again soon; an answered one waits a fresh drawn interval.
    _state.due[account.account_id] = moment + (
        _interval() if answered else timedelta(hours=limits.watch_retry_hours)
    )
    # The vault check needs no Telegram answer, so it runs whatever the audit said.
    await check_passkey_vault(account.account_id)
    if answered:
        await _check_passkey(account.account_id)
    return account.account_id


def reset_for_tests() -> None:
    """Forget the in-memory schedule between tests."""
    global _state  # noqa: PLW0603 - one module-level runtime, reset between tests
    _state = _State()
