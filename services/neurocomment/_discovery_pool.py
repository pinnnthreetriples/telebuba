"""The accounts one discovery run reads with, and which accounts may be picked at all.

One paced stream per account is the invariant the whole design rests on. A run that
reads with several accounts keeps it by ROTATING over them (``AccountPool``) rather
than by firing them in parallel; the gate that used to pick the listener automatically
(``check_search_account`` / ``list_search_accounts``) now answers per explicit pick,
because the operator chooses the accounts.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.db import fetch_account, fetch_warming_state, list_accounts, list_warming_account_ids
from core.repositories.neurocomment import get_listener_account_id, get_listener_running
from schemas.neurocomment_discovery_request import DiscoveryAccountList, DiscoveryAccountOption
from services.neurocomment import _discovery_state
from services.neurocomment._discovery_providers import account_cooling, record_flood
from services.neurocomment._state import in_cooldown
from services.trust import flood_active

if TYPE_CHECKING:
    from collections.abc import Iterable

    from schemas.accounts import AccountRead
    from schemas.neurocomment_discovery import DiscoveryStartStatus
    from schemas.neurocomment_discovery_request import DiscoveryBusyReason


@dataclass(frozen=True, slots=True)
class SearchAccount:
    """One account a run may read with; ``premium`` orders the rotation."""

    account_id: str
    premium: bool | None = None


class AccountPool:
    """Round-robin over a run's accounts, dropping each one the moment it turns unsafe.

    Premium accounts go first: Telegram's limits are looser for them, so they absorb
    the sweep's opening reads. An account leaves the rotation for good when a read
    floods it (its cooldown is recorded, as before), when someone else parked it
    (``account_cooling``), or when it failed ``discovery_max_consecutive_errors`` reads
    in a row — the dead-session rule the single-account run applied, now per account.
    The run stops when the pool is empty; ``dropped_reason`` says why the last one left.
    """

    def __init__(self, accounts: Iterable[SearchAccount]) -> None:
        ordered = sorted(accounts, key=lambda account: not account.premium)
        self._ids: deque[str] = deque(account.account_id for account in ordered)
        # The starting size, which is what the run's read budget is scaled by.
        self.size = len(self._ids)
        self._faults: dict[str, int] = {}
        self.dropped_reason: str | None = None

    @property
    def empty(self) -> bool:
        return not self._ids

    def acquire(self) -> str | None:
        """The next account to read with, or ``None`` when none is left.

        Re-read before EVERY read, not once per wave: a run is minutes long, and the
        comment engine can park any of its accounts at any point in it.
        """
        while self._ids:
            account_id = self._ids[0]
            self._ids.rotate(-1)
            if not account_cooling(account_id):
                return account_id
            self._drop(account_id, "cooling")
        return None

    async def report(
        self,
        account_id: str,
        *,
        flood_seconds: int | None = None,
        failed: bool = False,
    ) -> bool:
        """Record one read's outcome on its account; ``True`` means the pool is now empty."""
        if await record_flood(account_id, flood_seconds):
            self._drop(account_id, "flooded")
        elif failed:
            faults = self._faults.get(account_id, 0) + 1
            self._faults[account_id] = faults
            if faults >= settings.neurocomment.discovery_max_consecutive_errors:
                self._drop(account_id, "aborted")
        else:
            self._faults[account_id] = 0
        return self.empty

    def _drop(self, account_id: str, reason: str) -> None:
        if account_id in self._ids:
            self._ids.remove(account_id)
        self.dropped_reason = reason


class _Fleet(NamedTuple):
    """The fleet-wide facts every eligibility check reads, fetched once per call."""

    listener_id: str | None
    listener_running: bool
    warming: set[str]
    now: datetime


async def _fleet() -> _Fleet:
    return _Fleet(
        await get_listener_account_id(),
        await get_listener_running(),
        await list_warming_account_ids(),
        datetime.now(UTC),
    )


async def _blocker(account: AccountRead, fleet: _Fleet) -> DiscoveryBusyReason | None:
    """Why this account cannot search right now, or ``None`` when it may.

    Not named ``*_reason``: these are picker statuses the SPA labels on its own key, not
    ``log_event`` reasons, and the i18n parity guard reads ``*_reason`` returns as the
    latter.
    """
    if account.session_name is None:
        return "no_session"
    account_id = account.account_id
    # A *running* listener holds the session and reads continuously. Layering a
    # multi-minute paced keyword stream plus hundreds of probes on top of it is the same
    # mutual-exclusion violation the warming check below prevents.
    if fleet.listener_running and account_id == fleet.listener_id:
        return "account_busy"
    # Two independent health signals: the engine's in-memory cooldown (flood /
    # peer-flood / slow-mode) and warming's persisted flood deadline. Searching on a
    # cooling account would deepen the very limit it is serving out. Ahead of the
    # warming check: a warming account can also be flood-waiting, and both refuse, so the
    # order only picks which reason the operator is told.
    if in_cooldown(account_id, fleet.now):
        return "account_cooling"
    state = await fetch_warming_state(account_id)
    if state is not None and flood_active(state.flood_wait_until, fleet.now):
        return "account_cooling"
    # Warming assumes it owns its accounts' traffic — that assumption is the whole basis
    # of its freeze avoidance.
    if account_id in fleet.warming:
        return "account_busy"
    return None


_START_STATUS: dict[DiscoveryBusyReason, DiscoveryStartStatus] = {
    "no_session": "no_account",
    "account_busy": "account_busy",
    "account_cooling": "account_cooling",
}


async def check_search_account(account_id: str) -> SearchAccount | DiscoveryStartStatus:
    """One picked account as a run may use it, or the start status refusing it."""
    account = await fetch_account(account_id)
    if account is None:
        return "no_account"
    blocker = await _blocker(account, await _fleet())
    if blocker is not None:
        return _START_STATUS[blocker]
    return SearchAccount(account_id=account_id, premium=account.premium)


async def list_search_accounts() -> DiscoveryAccountList:
    """Every account as a pick for the search form, busy ones marked with why.

    Premium first, then by name — the order a pool reads them in.
    """
    fleet = await _fleet()
    items: list[DiscoveryAccountOption] = []
    for account in (await list_accounts()).accounts:
        blocker = await _blocker(account, fleet)
        if blocker is None and _discovery_state.account_busy(account.account_id):
            # Another campaign's run is reading with it right now.
            blocker = "account_busy"
        items.append(
            DiscoveryAccountOption(
                account_id=account.account_id,
                name=account.label or account.username or account.first_name or account.account_id,
                premium=account.premium,
                busy_reason=blocker,
            ),
        )
    items.sort(key=lambda item: (not item.premium, item.name.casefold()))
    return DiscoveryAccountList(items=items)


async def account_taken(account_id: str) -> bool:
    """Is another runtime holding this session? The re-read for under the claim lock.

    Both halves of ``check_search_account``'s ``account_busy`` verdict, asked again:
    that verdict is several awaits old by the time the claim is made, and either runtime
    can commit in the gap. Deliberately NOT reused inside ``_blocker`` — there the
    listener check runs before the health checks and the warming check after, so a
    warming account that is also flood-waiting is reported as cooling rather than busy.
    Folding the two into one call would silently reorder that.
    """
    if account_id in await list_warming_account_ids():
        return True
    return await get_listener_running() and await get_listener_account_id() == account_id
