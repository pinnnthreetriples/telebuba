"""Which accounts may read for a run, and the all-or-nothing reservation of them.

The channel-discovery rule, kept: every picked account is checked against ONE fleet
snapshot; the start then takes warming's per-account lifecycle lock for all of them in
sorted id order, asks every holder again inside it, and claims them all in the account
owner registry — or none. A refusal is a status naming the first account it hit, never an
exception.

Busy means another runtime is talking on the session: the running listener, warming, a
discovery run, or any holder in ``services._account_owner`` (neuroshilling, chat
broadcast, another parser run). Cooling means Telegram is rate-limiting it — the fleet
cooldown map or warming's persisted flood deadline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from core.db import list_accounts, list_warming_states
from core.repositories.neurocomment import get_listener_account_id, get_listener_running
from schemas.user_parser import (
    UserParserAccountList,
    UserParserAccountOption,
    UserParserStartOutcome,
)
from schemas.warming import is_warming
from services import _account_owner
from services.trust import flood_active
from services.user_parser import _seams

if TYPE_CHECKING:
    from datetime import datetime

    from schemas.accounts import AccountRead
    from schemas.user_parser import UserParserBusyReason

OWNER: _account_owner.Owner = "user_parser"


class _Fleet(NamedTuple):
    listener_id: str | None
    listener_running: bool
    warming: set[str]
    flood_waiting: set[str]
    now: datetime


async def _fleet() -> _Fleet:
    now = _seams.now()
    states = await list_warming_states()
    return _Fleet(
        await get_listener_account_id(),
        await get_listener_running(),
        {record.account_id for record in states if is_warming(record.state)},
        {record.account_id for record in states if flood_active(record.flood_wait_until, now)},
        now,
    )


def _discovery_holds(account_id: str) -> bool:
    from services.neurocomment import _discovery_state  # noqa: PLC0415 - import cycle

    return _discovery_state.account_busy(account_id)


def _held(account_id: str, fleet: _Fleet) -> bool:
    return (
        (fleet.listener_running and account_id == fleet.listener_id)
        or account_id in fleet.warming
        or _discovery_holds(account_id)
        or _account_owner.owner_of(account_id) is not None
    )


def _blocker(account: AccountRead, fleet: _Fleet) -> UserParserBusyReason | None:
    if account.session_name is None:
        return "no_session"
    account_id = account.account_id
    if _seams.in_cooldown(account_id) or account_id in fleet.flood_waiting:
        return "account_cooling"
    if _held(account_id, fleet):
        return "account_busy"
    return None


def display_name(account: AccountRead) -> str:
    """The SPA's ``accountDisplayName`` rule, so the picker and the table agree."""
    full = " ".join(part for part in (account.first_name, account.last_name) if part)
    return full or account.phone or account.account_id


async def list_reading_accounts() -> UserParserAccountList:
    """Every account as a pick, busy ones marked with why; Premium first, then by name."""
    fleet = await _fleet()
    items = [
        UserParserAccountOption(
            account_id=account.account_id,
            name=display_name(account),
            username=account.username,
            premium=account.premium,
            busy_reason=_blocker(account, fleet),
        )
        for account in (await list_accounts()).accounts
    ]
    items.sort(key=lambda item: (not item.premium, item.name.casefold()))
    return UserParserAccountList(items=items)


def _refusal(account_id: str, reason: UserParserBusyReason) -> UserParserStartOutcome:
    if reason == "no_session":
        return UserParserStartOutcome(status="no_account", refused_account_id=account_id)
    if reason == "account_busy" and _account_owner.owner_of(account_id) == OWNER:
        return UserParserStartOutcome(status="already_running", refused_account_id=account_id)
    return UserParserStartOutcome(status=reason, refused_account_id=account_id)


async def check_accounts(account_ids: list[str]) -> dict[str, str] | UserParserStartOutcome:
    """The picked accounts by name, or the outcome refusing the first one that cannot read."""
    fleet = await _fleet()
    known = {account.account_id: account for account in (await list_accounts()).accounts}
    names: dict[str, str] = {}
    for account_id in account_ids:
        account = known.get(account_id)
        if account is None:
            return UserParserStartOutcome(status="no_account", refused_account_id=account_id)
        reason = _blocker(account, fleet)
        if reason is not None:
            return _refusal(account_id, reason)
        names[account_id] = display_name(account)
    return names


async def claim_accounts(run_id: str, account_ids: list[str]) -> str | None:
    """Re-ask every holder and claim all accounts for ``run_id``; the first taken one, or None.

    The caller holds ``account_lock`` for every id. All or nothing: a refused claim gives
    back the ones this call already took.
    """
    fleet = await _fleet()
    claimed: list[str] = []
    for account_id in account_ids:
        if _held(account_id, fleet) or _account_owner.try_claim(account_id, OWNER, run_id):
            for taken in claimed:
                _account_owner.release(taken, OWNER, run_id)
            return account_id
        claimed.append(account_id)
    return None


def release_accounts(run_id: str, account_ids: list[str]) -> None:
    for account_id in account_ids:
        _account_owner.release(account_id, OWNER, run_id)
