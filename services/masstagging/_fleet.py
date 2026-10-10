"""The campaign's accounts as the engine and the board see them.

An account works only when it is alive and has no spam block: a spam-blocked account
still posts the story, but the people it mentions get no push (TgNinja), so it stays
on the roster and shows as excluded. The spam block is the account's stored verdict —
no new probe. An account another feature drives is left out too; the board names who
holds it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.db import list_accounts_by_ids, list_warming_account_ids
from core.repositories import masstagging as repository
from core.repositories.chat_broadcast import list_running_account_names as broadcast_rosters
from core.repositories.neurocomment import (
    get_listener_account_id,
    get_listener_running,
    list_active_campaign_account_names,
)
from core.repositories.neuroshilling import list_running_campaign_account_names
from core.repositories.spam_status import list_spam_statuses_by_ids
from services import _account_owner
from services.masstagging.pace import AccountView, day_of, in_ramp

if TYPE_CHECKING:
    from schemas.masstagging import MasstaggingPace
    from schemas.masstagging_board import MasstaggingBusyOwner

_OWNER = "masstagging"


@dataclass(frozen=True)
class FleetAccount:
    account_id: str
    alive: bool
    spamblock: bool
    premium: bool

    @property
    def usable(self) -> bool:
        return self.alive and not self.spamblock


async def fleet_of(account_ids: list[str]) -> dict[str, FleetAccount]:
    """Each roster account's standing; an account deleted meanwhile is not alive."""
    if not account_ids:
        return {}
    accounts = {
        item.account_id: item for item in (await list_accounts_by_ids(account_ids)).accounts
    }
    spam = await list_spam_statuses_by_ids(account_ids)
    fleet: dict[str, FleetAccount] = {}
    for account_id in account_ids:
        account = accounts.get(account_id)
        verdict = spam.get(account_id)
        fleet[account_id] = FleetAccount(
            account_id=account_id,
            alive=account is not None and account.status == "alive",
            spamblock=verdict is not None and verdict.status == "limited",
            premium=bool(account is not None and account.premium),
        )
    return fleet


async def views(
    fleet: dict[str, FleetAccount], pace: MasstaggingPace, now: int
) -> dict[str, AccountView]:
    """Today's counters and the soft-start flag of each account."""
    ids = list(fleet)
    today = day_of(now)
    days = await repository.fetch_days(ids, today)
    firsts = await repository.first_days(ids)
    return {
        account_id: AccountView(
            account_id=account_id,
            premium=account.premium,
            day=days[account_id],
            ramp=in_ramp(firsts.get(account_id), today, enabled=pace.ramp_up),
        )
        for account_id, account in fleet.items()
    }


async def _durable_holders(campaign_id: str) -> dict[str, MasstaggingBusyOwner]:
    """The rows that back the registry up while a process has only just started."""
    busy: dict[str, MasstaggingBusyOwner] = {}
    for account_id in await list_active_campaign_account_names():
        busy[account_id] = "neurocomment"
    if await get_listener_running() and (listener := await get_listener_account_id()):
        busy[listener] = "neurocomment"
    for account_id in await list_warming_account_ids():
        busy[account_id] = "warming"
    for account_id in await list_running_campaign_account_names():
        busy[account_id] = "neuroshilling"
    for account_id in await broadcast_rosters():
        busy[account_id] = "chat_broadcast"
    for account_id, (holder, _name) in (await repository.list_running_account_names()).items():
        if holder != campaign_id:
            busy[account_id] = "masstagging"
    return busy


async def busy_owners(campaign_id: str, account_ids: list[str]) -> dict[str, MasstaggingBusyOwner]:
    """Which of ``account_ids`` another feature is driving right now.

    The sources the broadcast's picker reads, plus this registry while runs are in
    flight. This campaign's own hold is not "busy".
    """
    from services.neurocomment import _discovery_state  # noqa: PLC0415 - import cycle

    wanted = set(account_ids)
    busy = await _durable_holders(campaign_id)
    for account_id, owner in _account_owner.owners().items():
        if owner == _OWNER and _account_owner.holder_of(account_id) == campaign_id:
            continue
        busy[account_id] = owner
    for account_id in [a for a in wanted if _discovery_state.account_busy(a)]:
        busy[account_id] = "discovery"
    return {account_id: owner for account_id, owner in busy.items() if account_id in wanted}
