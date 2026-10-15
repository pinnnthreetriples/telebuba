"""Which account a pool search may read with — free first, a busy one only with consent.

A pool search («Найти каналы» or the automatic refill) never reads with an account in
warming: a warming account only ever searches for itself, inside its own visit
(``_self_search``). An account warming stopped on a confirmed spam block is not in
warming — no loop runs for it — and a spam block only shuts messages to strangers, never
searching, joining or reading channels, so it searches like any other account (the
«Спамблок» mark stays on its card). Every other account falls into one of three groups:

- **free**: alive, with a session, and nothing else is using it — no campaign of any
  feature holds it, it serves no active neurocomment campaign, it is not the running
  listener, no discovery or user-parser run reads with it, and no flood is cooling it;
- **pullable**: as free, except that neurocomment (commenting), a bulk message run,
  neuroshilling, a chat broadcast or masstagging is using it. Such an account is only
  ever taken after the operator agreed in the consent dialog, and is given back when
  the search ends;
- **unusable**: everything else — in warming (a spam-block stop aside), the listener, a
  discovery or parser run, cooling, dead. Never taken, not even with consent.

The pull order puts the work that suffers least from a pause first: a skipped post or
two of commenting, a bulk run's next message a few minutes late, then masstagging and
broadcast rounds, and a scripted neuroshilling dialogue last.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from core.db import list_accounts, list_warming_states
from core.repositories.bulk_runs import list_bulk_run_accounts, list_live_bulk_runs
from core.repositories.neurocomment import list_active_campaign_account_names
from schemas._warming_pool import PoolSearchCandidate
from schemas.warming import is_warming
from services import _account_owner
from services.neurocomment import _discovery_state
from services.neurocomment._state import in_cooldown
from services.neuroshilling._listener_lookup import running_listener_account_id
from services.trust import flood_active

if TYPE_CHECKING:
    from schemas._warming_pool import PoolSearchBusyWith
    from schemas.accounts import AccountRead
    from schemas.warming import WarmingStateRecord

_PULL_ORDER: Final[tuple[PoolSearchBusyWith, ...]] = (
    "neurocomment",
    "bulk_messages",
    "masstagging",
    "chat_broadcast",
    "neuroshilling",
)
# The registry owners whose work pauses for a search (their claim stays underneath).
_PULLABLE_OWNERS: Final[dict[str, PoolSearchBusyWith]] = {
    "neuroshilling": "neuroshilling",
    "chat_broadcast": "chat_broadcast",
    "masstagging": "masstagging",
    "bulk_messages": "bulk_messages",
}


@dataclass(frozen=True, slots=True)
class _Fleet:
    """The fleet-wide facts one pick reads, fetched once."""

    accounts: dict[str, AccountRead]
    states: dict[str, WarmingStateRecord]
    commenting: set[str]
    # Accounts of a live bulk message run. The run claims them in the registry when its
    # loop starts; the rows also cover a run a restart has not resumed yet.
    bulk: set[str]
    listener: str | None
    now: datetime


async def _bulk_accounts() -> set[str]:
    return {
        item.account_id
        for run in await list_live_bulk_runs()
        for item in await list_bulk_run_accounts(run.id)
    }


async def _fleet() -> _Fleet:
    return _Fleet(
        accounts={a.account_id: a for a in (await list_accounts()).accounts},
        states={record.account_id: record for record in await list_warming_states()},
        commenting=set(await list_active_campaign_account_names()),
        bulk=await _bulk_accounts(),
        listener=await running_listener_account_id(),
        now=datetime.now(UTC),
    )


def _in_warming(state: WarmingStateRecord) -> bool:
    """A warming loop owns the account; a spam-block stop does not (no loop runs)."""
    return is_warming(state.state) and state.state != "spam_blocked"


def _usable(account_id: str, fleet: _Fleet) -> bool:
    """Alive, with a session, outside warming, and no reader that cannot be paused."""
    account = fleet.accounts.get(account_id)
    if account is None or account.status != "alive" or account.session_name is None:
        return False
    state = fleet.states.get(account_id)
    if state is not None and (
        _in_warming(state) or flood_active(state.flood_wait_until, fleet.now)
    ):
        return False
    owner = _account_owner.owner_of(account_id)
    return (
        account_id != fleet.listener
        and owner not in {"warming", "user_parser"}
        and not _account_owner.is_lent(account_id)
        and not _discovery_state.account_busy(account_id)
        and not in_cooldown(account_id, fleet.now)
    )


def _busy_with(account_id: str, fleet: _Fleet) -> PoolSearchBusyWith | None:
    """What a usable account is doing now; ``None`` when it is free."""
    held = _PULLABLE_OWNERS.get(_account_owner.owner_of(account_id) or "")
    if held is not None:
        return held
    if account_id in fleet.bulk:
        return "bulk_messages"
    return "neurocomment" if account_id in fleet.commenting else None


def display_name(account: AccountRead) -> str:
    """The accounts table's name: Telegram first + last name, else phone, else id."""
    full = " ".join(part for part in (account.first_name, account.last_name) if part)
    return full or account.phone or account.account_id


def _free_order(account_ids: list[str], fleet: _Fleet) -> list[str]:
    """Accounts handed to neurocomment first: warmed, with history, made for reading."""
    handed = [a for a in account_ids if (s := fleet.states.get(a)) is not None and s.nc_handed_off]
    return handed + sorted(set(account_ids) - set(handed))


async def free_search_accounts() -> list[str]:
    """Every account a pool search may take without asking anyone, preferred first."""
    fleet = await _fleet()
    free = sorted(a for a in fleet.accounts if _usable(a, fleet) and _busy_with(a, fleet) is None)
    return _free_order(free, fleet)


async def pull_candidates() -> list[PoolSearchCandidate]:
    """The busy accounts a search could take with consent, the least disruptive first."""
    fleet = await _fleet()
    found: list[PoolSearchCandidate] = []
    for account_id in sorted(fleet.accounts):
        if not _usable(account_id, fleet):
            continue
        busy = _busy_with(account_id, fleet)
        if busy is not None:
            found.append(
                PoolSearchCandidate(
                    account_id=account_id,
                    name=display_name(fleet.accounts[account_id]),
                    busy_with=busy,
                )
            )
    return sorted(found, key=lambda c: _PULL_ORDER.index(c.busy_with))


async def search_standing(account_id: str) -> PoolSearchCandidate | str | None:
    """Where one account stands right now: free (its id), pullable, or ``None``.

    Asked again under the account's lifecycle lock, right before the search borrows it:
    the earlier pick is several awaits old by then.
    """
    fleet = await _fleet()
    if not _usable(account_id, fleet):
        return None
    busy = _busy_with(account_id, fleet)
    if busy is None:
        return account_id
    return PoolSearchCandidate(
        account_id=account_id,
        name=display_name(fleet.accounts[account_id]),
        busy_with=busy,
    )
