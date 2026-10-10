"""The board: the pipeline's numbers, the accounts, one row per person with its history.

Everything is read in one pass and composed here; the SPA only words the codes and
fixes the order of the rows (once per load). A campaign never started has no rows of
its own yet, so its saved list is shown as the queue it will become.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.config import settings
from core.repositories import masstagging as repository
from schemas.masstagging_board import (
    MasstaggingBoard,
    MasstaggingBoardAccount,
    MasstaggingBoardRow,
    MasstaggingCounters,
    MasstaggingHistoryEntry,
)
from services.masstagging import _fleet, _moves, _seams, _state
from services.masstagging._context import pace_of
from services.masstagging.campaigns import settings_of, to_campaign
from services.masstagging.pace import daily_reach, left_today, next_day_start
from services.masstagging.targets import seeds_of

if TYPE_CHECKING:
    from schemas.masstagging import MasstaggingMode, MasstaggingPace
    from schemas.masstagging_board import MasstaggingBusyOwner
    from schemas.masstagging_records import JournalRecord, TargetRecord
    from services.masstagging._fleet import FleetAccount
    from services.masstagging.pace import AccountView


def _at(unix: int | None) -> datetime | None:
    return None if unix is None else datetime.fromtimestamp(unix, UTC)


async def load_board(campaign_id: str) -> MasstaggingBoard | None:
    record = await repository.fetch_campaign(campaign_id)
    if record is None:
        return None
    parsed = settings_of(record)
    pace = _state.live_pace(campaign_id) or pace_of(parsed)
    roster = await repository.list_roster(campaign_id)
    fleet = await _fleet.fleet_of(roster)
    busy = await _fleet.busy_owners(campaign_id, roster)
    now = _seams.now()
    views = await _fleet.views(fleet, pace, now)
    accounts = [_account(fleet[a], views[a], busy.get(a), parsed.mode, pace, now) for a in roster]
    targets = await repository.list_targets(campaign_id)
    if record.run_status == "idle" and not targets:
        rows = [_preview(seed.target_key, seed.raw) for seed in seeds_of(parsed.targets)]
    else:
        rows = _rows(targets, await repository.list_journal(campaign_id))
    reach = sum(
        daily_reach(views[a.account_id], parsed.mode, pace)
        for a in accounts
        if a.state not in {"spamblock", "not_alive", "busy"}
    )
    return MasstaggingBoard(
        campaign=to_campaign(record, roster, fleet),
        next_action_at=_at(_state.next_step(campaign_id)),
        accounts=accounts,
        counters=_counters(rows, accounts, reach),
        rows=rows,
    )


def _account(  # noqa: PLR0913 - one row joins the fleet, the day and the registry
    account: FleetAccount,
    view: AccountView,
    owner: MasstaggingBusyOwner | None,
    mode: MasstaggingMode,
    pace: MasstaggingPace,
    now: int,
) -> MasstaggingBoardAccount:
    row = MasstaggingBoardAccount(
        account_id=account.account_id, state="working", premium=account.premium
    )
    if not account.alive:
        return row.model_copy(update={"state": "not_alive"})
    if account.spamblock:
        return row.model_copy(update={"state": "spamblock"})
    if owner is not None:
        return row.model_copy(update={"state": "busy", "busy_owner": owner})
    paused = view.day.paused_until_unix
    if left_today(view, mode, pace) <= 0:
        return row.model_copy(update={"state": "resting", "until": _at(next_day_start(now))})
    if paused is not None and paused > now:
        return row.model_copy(update={"state": "flood", "until": _at(paused)})
    return row


def _preview(key: str, raw: str) -> MasstaggingBoardRow:
    return MasstaggingBoardRow(target_key=key, target=raw, state="queued", active=True, history=[])


def _when(target: TargetRecord) -> int | None:
    if target.state == "queued":
        return target.next_action_unix
    if target.state == "flood":
        return target.next_action_unix
    return target.updated_unix


def _rows(targets: list[TargetRecord], journal: list[JournalRecord]) -> list[MasstaggingBoardRow]:
    history: dict[str, list[JournalRecord]] = defaultdict(list)
    for entry in journal:
        history[entry.target_key].append(entry)
    keep = settings.masstagging.history_per_target
    return [
        MasstaggingBoardRow(
            target_key=target.target_key,
            target=target.raw,
            state=target.state,
            reason=target.reason if target.state in {"skipped", "failed"} else None,
            when=_at(_when(target)),
            account_id=target.account_id,
            active=target.state in _moves.ACTIVE_STATES,
            history=[
                MasstaggingHistoryEntry(
                    at=datetime.fromtimestamp(entry.at_unix, UTC),
                    account_id=entry.account_id,
                    event=entry.event,
                    detail=entry.detail,
                )
                for entry in history[target.target_key][-keep:]
            ],
        )
        for target in targets
    ]


def _counters(
    rows: list[MasstaggingBoardRow], accounts: list[MasstaggingBoardAccount], reach: int
) -> MasstaggingCounters:
    left = sum(1 for row in rows if row.active)
    excluded = sum(1 for a in accounts if a.state in {"spamblock", "not_alive"})
    working = [a for a in accounts if a.state not in {"spamblock", "not_alive", "busy"}]
    return MasstaggingCounters(
        total=len(rows),
        tagged=sum(1 for row in rows if row.state == "tagged"),
        left=left,
        failed=sum(1 for row in rows if row.state == "failed"),
        skipped=sum(1 for row in rows if row.state == "skipped"),
        per_day=reach,
        days_left=math.ceil(left / reach) if reach and left else None,
        working_accounts=len(working),
        excluded_accounts=excluded,
        free_accounts=sum(1 for a in working if not a.premium),
    )
