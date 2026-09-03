"""The accounts one discovery run reads with, and which accounts may be picked at all.

One paced stream per account is the invariant the whole design rests on. A run that
reads with several accounts keeps it by running one concurrent stream PER account
(``services.neurocomment._discovery_streams.Streams``) rather than rotating a single
paced reader over them; this pool answers each stream's own eligibility checks
(``check``, ``premium_left``) instead of handing out a shared next-account turn. The
gate that used to pick the listener automatically (``check_search_accounts`` /
``list_search_accounts``) now answers per explicit pick, because the operator chooses
the accounts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, NamedTuple

from core.config import settings
from core.db import list_accounts, list_warming_states
from core.repositories.neurocomment import get_listener_account_id, get_listener_running
from schemas.neurocomment_discovery import DiscoverySearchOutcome
from schemas.neurocomment_discovery_request import DiscoveryAccountList, DiscoveryAccountOption
from schemas.warming import is_warming
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
    """One account a run may read with; ``premium`` marks the preferred pick for some reads."""

    account_id: str
    premium: bool | None = None
    name: str = ""


class AccountPool:
    """The accounts one discovery run reads with, dropping each one the moment it turns unsafe.

    No rotation: each account is its own concurrent stream
    (``services.neurocomment._discovery_streams.Streams``), so this pool only answers
    per-stream questions — which accounts are still here (``accounts``, ``has``), is a
    Premium one still usable (``premium_left``), and may THIS account take THIS read
    right now (``check``).

    An account leaves the pool for good when a read floods it (its cooldown is
    recorded, as before), when someone else parked it (``account_cooling``), when it
    failed ``discovery_max_consecutive_errors`` reads in a row — the dead-session rule
    the single-account run applied, now per account — or when the client pool could
    not connect it at all (``unreachable``): that one drops on the spot, no three
    strikes, because a session that never answers is not a session degrading. The run
    stops when the pool is empty.

    Wave reads are also CAPPED per account at ``discovery_max_reads_per_run``: the
    run's shared budget is that ceiling times the starting size, and without this cap a
    lone survivor of two dropped accounts absorbed all three shares. An account at its
    ceiling stays in the pool — qualification probes (``charge=False``) are bounded by
    the candidate limit, not by this — but no wave read is handed to it, and a wave that
    finds every account capped is truncated, not stopped.
    """

    def __init__(self, accounts: Iterable[SearchAccount]) -> None:
        self._accounts: dict[str, SearchAccount] = {
            account.account_id: account for account in accounts
        }
        # The starting size, which is what the run's read budget is scaled by.
        self.size = len(self._accounts)
        self._faults: dict[str, int] = {}
        self._reads: dict[str, int] = {}

    @property
    def empty(self) -> bool:
        return not self._accounts

    def accounts(self) -> list[SearchAccount]:
        """Every account still in the pool, in the order the run was started with."""
        return list(self._accounts.values())

    def has(self, account_id: str) -> bool:
        return account_id in self._accounts

    def premium_left(self) -> bool:
        """Is a Premium account still usable for a read that prefers one?

        Read live off the per-account read count, not off pool membership: an account
        at its wave ceiling is still ``has()`` (probes may still use it) but can take no
        more charged reads, so a stream waiting on ``prefer_premium`` must fall back to
        a plain account the moment the last Premium one caps out — not only once it is
        flooded or dropped.
        """
        ceiling = settings.neurocomment.discovery_max_reads_per_run
        return any(
            account.premium and self._reads.get(account.account_id, 0) < ceiling
            for account in self._accounts.values()
        )

    def check(self, account_id: str, *, charge: bool) -> Literal["ok", "cooling", "capped"]:
        """May this account take the read it was just picked for, right now?

        Called AFTER the stream's own pace sleep, like the old ``acquire`` was: a run
        is minutes long, and the comment engine (or the run's own later reads) can park
        the account at any point in it. ``cooling`` drops the account for good.
        ``capped`` leaves it in the pool (qualification probes are bounded elsewhere)
        but refuses this charged read. ``charge=False`` (a probe) never caps and never
        spends the ceiling.
        """
        if account_cooling(account_id):
            self._drop(account_id)
            return "cooling"
        reads = self._reads.get(account_id, 0)
        if charge and reads >= settings.neurocomment.discovery_max_reads_per_run:
            return "capped"
        if charge:
            self._reads[account_id] = reads + 1
        return "ok"

    async def report(
        self,
        account_id: str,
        *,
        flood_seconds: int | None = None,
        failed: bool = False,
        unreachable: bool = False,
    ) -> str | None:
        """Record one read's outcome on its account.

        ``unreachable`` drops the account on the spot: the client pool never even
        reached Telegram, so there is nothing to count towards the consecutive-error
        threshold — a session that will not connect at all does not deserve two more
        tries to prove it, the way an ordinary failed read does.

        Returns the reason the pool is now empty (``flooded`` / ``aborted``), or ``None``
        while at least one account is left to read with.
        """
        stop = None
        if unreachable:
            stop = "aborted"
        elif await record_flood(account_id, flood_seconds):
            stop = "flooded"
        elif failed:
            faults = self._faults.get(account_id, 0) + 1
            self._faults[account_id] = faults
            if faults >= settings.neurocomment.discovery_max_consecutive_errors:
                stop = "aborted"
        else:
            self._faults[account_id] = 0
        if stop is not None:
            self._drop(account_id)
        return stop if self.empty else None

    def _drop(self, account_id: str) -> None:
        self._accounts.pop(account_id, None)


class _Fleet(NamedTuple):
    """The fleet-wide facts every eligibility check reads, fetched once per call."""

    listener_id: str | None
    listener_running: bool
    warming: set[str]
    flood_waiting: set[str]
    now: datetime


async def _fleet() -> _Fleet:
    now = datetime.now(UTC)
    states = await list_warming_states()
    return _Fleet(
        await get_listener_account_id(),
        await get_listener_running(),
        {record.account_id for record in states if is_warming(record.state)},
        {record.account_id for record in states if flood_active(record.flood_wait_until, now)},
        now,
    )


def _blocker(
    account: AccountRead,
    fleet: _Fleet,
    campaign_id: str | None = None,
) -> DiscoveryBusyReason | None:
    """Why this account cannot search right now, or ``None`` when it may.

    ``campaign_id`` is the campaign asking: its own run holding the account is
    ``already_running``, which the claim reports, not a busy account. Not named
    ``*_reason``: these are picker statuses the SPA labels on its own key, not
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
    if in_cooldown(account_id, fleet.now) or account_id in fleet.flood_waiting:
        return "account_cooling"
    # Warming assumes it owns its accounts' traffic — that assumption is the whole basis
    # of its freeze avoidance.
    if account_id in fleet.warming:
        return "account_busy"
    # Another campaign's run is reading with it right now.
    if _discovery_state.account_busy(account_id, other_than=campaign_id):
        return "account_busy"
    return None


_START_STATUS: dict[DiscoveryBusyReason, DiscoveryStartStatus] = {
    "no_session": "no_account",
    "account_busy": "account_busy",
    "account_cooling": "account_cooling",
}


async def check_search_accounts(
    campaign_id: str,
    account_ids: list[str],
) -> list[SearchAccount] | DiscoverySearchOutcome:
    """The picked accounts as a run may use them, or the outcome refusing the first bad one.

    One fleet snapshot for the whole pick, not one per account.
    """
    fleet = await _fleet()
    known = {account.account_id: account for account in (await list_accounts()).accounts}
    accounts: list[SearchAccount] = []
    for account_id in account_ids:
        account = known.get(account_id)
        if account is None:
            return DiscoverySearchOutcome(status="no_account", refused_account_id=account_id)
        blocker = _blocker(account, fleet, campaign_id)
        if blocker is not None:
            return DiscoverySearchOutcome(
                status=_START_STATUS[blocker],
                refused_account_id=account_id,
            )
        accounts.append(
            SearchAccount(
                account_id=account_id,
                premium=account.premium,
                name=_display_name(account),
            )
        )
    return accounts


def _display_name(account: AccountRead) -> str:
    """The SPA's ``accountDisplayName`` rule, so the picker and the table agree."""
    full = " ".join(part for part in (account.first_name, account.last_name) if part)
    return full or account.phone or account.account_id


async def list_search_accounts() -> DiscoveryAccountList:
    """Every account as a pick for the search form, busy ones marked with why.

    Premium first, then by name — the order the pool prefers them in. Named as the
    accounts table names them (Telegram first + last name, else phone, else id): the
    import label is a file name like ``1_telethon`` that tells the operator nothing
    once the fleet is fifty accounts.
    """
    fleet = await _fleet()
    items = [
        DiscoveryAccountOption(
            account_id=account.account_id,
            name=_display_name(account),
            username=account.username,
            premium=account.premium,
            busy_reason=_blocker(account, fleet),
        )
        for account in (await list_accounts()).accounts
    ]
    items.sort(key=lambda item: (not item.premium, item.name.casefold()))
    return DiscoveryAccountList(items=items)


async def taken_account(campaign_id: str, account_ids: list[str]) -> str | None:
    """The first of these another runtime holds, or ``None``. The re-read under the claim lock.

    Every holder behind ``check_search_accounts``'s ``account_busy`` verdict — warming,
    the running listener, another campaign's run — asked again: that verdict is several
    awaits old by the time the claim is made, and any of them can commit in the gap.
    Without the third, a run another campaign started in that gap was caught by the claim
    itself, as ``already_running`` naming no account — which points the operator at THIS
    campaign, which has no run. Read ONCE inside the locks, not per account, but
    deliberately NOT reused inside ``_blocker`` — there the listener check runs before the
    health checks and the warming check after, so a warming account that is also
    flood-waiting is reported as cooling rather than busy. Folding the two would silently
    reorder that.
    """
    fleet = await _fleet()
    for account_id in account_ids:
        if (
            account_id in fleet.warming
            or (fleet.listener_running and fleet.listener_id == account_id)
            or _discovery_state.account_busy(account_id, other_than=campaign_id)
        ):
            return account_id
    return None
