"""The re-check: an account whose last session check could not reach Telegram is asked again.

A flood wait, a proxy or a network fault leaves ``status`` as it was and sets ``link_status``
(see ``core.repositories._accounts_session_check``). Nothing else looks at such an account
until the operator presses "check", so the guard does: one account per pass, behind a proxy,
not lent, its proxy not read within the watch's spacing (the stamp is shared with the watch),
and a per-account wait that starts at ``recheck_first_minutes`` and doubles per failed attempt
up to ``recheck_max_hours``. An answer clears the wait. Memory only (one uvicorn worker).

Never raises: a failed attempt is logged by type name and counted like any other.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from core.config import settings
from core.logging import log_event
from core.repositories.accounts import list_accounts
from schemas.accounts import AccountCheckRequest, AccountRead
from services._account_owner import is_lent
from services.accounts import check_account_session
from services.session_guard import _watch

logger = logging.getLogger(__name__)

_TRANSPORT = frozenset({"flood_wait", "network_error", "proxy_error", "unknown_error"})


@dataclass
class _State:
    attempts: dict[str, int] = field(default_factory=dict)
    next_at: dict[str, datetime] = field(default_factory=dict)


_state = _State()


def _failed_link(account: AccountRead) -> bool:
    # A legacy row carries the transport value in ``status`` itself.
    return account.link_status is not None or account.status in _TRANSPORT


def _wait(attempts: int) -> timedelta:
    limits = settings.session_audit
    minutes = limits.recheck_first_minutes * 2 ** max(attempts - 1, 0)
    return timedelta(minutes=min(minutes, limits.recheck_max_hours * 60))


def _pick(accounts: list[AccountRead], now: datetime) -> AccountRead | None:
    spacing = timedelta(seconds=settings.session_audit.watch_proxy_spacing_seconds)
    asked = _watch._state.asked_by_proxy  # noqa: SLF001 - one proxy stamp shared with the watch
    for account in accounts:
        if account.proxy_id is None or is_lent(account.account_id) or not _failed_link(account):
            continue
        if _state.next_at.get(account.account_id, now) > now:
            continue
        last = asked.get(account.proxy_id)
        if last is None or now - last >= spacing:
            return account
    return None


async def run_recheck_pass(*, now: datetime | None = None) -> str | None:
    """Check one link-failed account again; its id, or ``None``. Never raises."""
    moment = now or datetime.now(UTC)
    try:
        account = _pick((await list_accounts()).accounts, moment)
        if account is None:
            return None
        _watch._state.asked_by_proxy[account.proxy_id or ""] = moment  # noqa: SLF001
        await _attempt(account, moment)
    except Exception as exc:  # noqa: BLE001 - the guard step must never end on a re-check
        logger.warning("session re-check pass failed (%s)", type(exc).__name__)
        return None
    return account.account_id


async def _attempt(account: AccountRead, moment: datetime) -> None:
    account_id = account.account_id
    saved: AccountRead | None = None
    try:
        saved = await check_account_session(AccountCheckRequest(account_id=account_id))
    except Exception as exc:  # noqa: BLE001 - counted as a failed attempt, never raised
        logger.warning("session re-check failed (%s)", type(exc).__name__)
    if saved is None or saved.link_status is not None or saved.status in _TRANSPORT:
        _state.attempts[account_id] = _state.attempts.get(account_id, 0) + 1
        _state.next_at[account_id] = moment + _wait(_state.attempts[account_id])
        return
    _state.attempts.pop(account_id, None)
    _state.next_at.pop(account_id, None)
    if saved.status == "alive":
        await log_event("INFO", "account_link_restored", account_id=account_id)


def reset_for_tests() -> None:
    """Forget the in-memory back-off between tests."""
    global _state  # noqa: PLW0603 - one module-level runtime, reset between tests
    _state = _State()
