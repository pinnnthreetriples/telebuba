"""The board: one row per chat with its account, progress, status and history.

Everything is read in one pass — the campaign, roster, chats, the run's journal and the
history — and composed here; the SPA only words the codes. The order of the rows is the
SPA's to fix (by importance, once per load), so this returns the run's own order.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Literal

from core.config import settings
from core.repositories import chat_broadcast as repository
from schemas.chat_broadcast_board import (
    ChatBroadcastBoard,
    ChatBroadcastBoardAccount,
    ChatBroadcastBoardRow,
    ChatBroadcastCounters,
    ChatBroadcastHistoryEntry,
    ChatBroadcastPhase,
)
from services.chat_broadcast import _seams
from services.chat_broadcast._context import usable_chain
from services.chat_broadcast.campaigns import busy_owners, settings_of, to_campaign

if TYPE_CHECKING:
    from schemas.chat_broadcast_board import ChatBroadcastBusyOwner
    from schemas.chat_broadcast_records import (
        CampaignRecord,
        EventRecord,
        JournalRecord,
        RosterAccount,
        TargetRecord,
    )

_DELIVERED: Final = frozenset({"sent", "unconfirmed", "deleted"})
_JOINING: Final = frozenset({"joining", "captcha", "pending_approval"})


def _at(unix: int | None) -> datetime | None:
    return None if unix is None else datetime.fromtimestamp(unix, UTC)


async def load_board(campaign_id: str) -> ChatBroadcastBoard | None:
    record = await repository.fetch_campaign(campaign_id)
    if record is None:
        return None
    parsed = settings_of(record)
    chain_length = len(usable_chain(parsed))
    rounds = None if parsed.loop and parsed.rounds == 0 else (parsed.rounds if parsed.loop else 1)
    roster = await repository.list_roster(campaign_id)
    busy = await busy_owners(campaign_id, [item.account_id for item in roster])
    targets = await repository.list_targets(campaign_id)
    journal = await repository.list_journal(campaign_id, record.run_id) if record.run_id else []
    events = await repository.list_events(campaign_id)
    accounts = [_account(item, busy) for item in roster]
    return ChatBroadcastBoard(
        campaign=to_campaign(record),
        phase=_phase(record, targets),
        chain_length=chain_length,
        started_at=_at(record.started_unix),
        resumed_at=_at(record.resumed_unix),
        finished_at=_at(record.finished_unix),
        accounts=accounts,
        counters=_counters(accounts, targets, journal, chain_length, rounds),
        rows=_rows(record, targets, journal, events, chain_length * rounds if rounds else None),
    )


def _account(
    item: RosterAccount, busy: dict[str, ChatBroadcastBusyOwner]
) -> ChatBroadcastBoardAccount:
    state: Literal["active", "halted", "busy"] = "active"
    if item.state == "halted":
        state = "halted"
    elif item.account_id in busy:
        state = "busy"
    return ChatBroadcastBoardAccount(
        account_id=item.account_id,
        state=state,
        halted_reason=item.halted_reason,
        busy_owner=busy.get(item.account_id),
    )


def _counters(
    accounts: list[ChatBroadcastBoardAccount],
    targets: list[TargetRecord],
    journal: list[JournalRecord],
    chain_length: int,
    rounds: int | None,
) -> ChatBroadcastCounters:
    chats = [t for t in targets if t.skip_reason != "removed"]
    return ChatBroadcastCounters(
        accounts=len(accounts),
        accounts_working=sum(1 for account in accounts if account.state == "active"),
        chats=len(chats),
        joined=sum(1 for t in chats if t.member_account_id is not None),
        pending_approval=sum(1 for t in chats if t.state == "pending_approval"),
        waiting=sum(1 for t in chats if t.state == "waiting"),
        sent=sum(1 for row in journal if row.status in _DELIVERED),
        skipped=sum(1 for t in chats if t.state == "skipped"),
        handed=sum(1 for t in chats if t.handed_from_account_id is not None),
        planned=len(chats) * chain_length * rounds if rounds else None,
        rounds=rounds,
    )


def _phase(record: CampaignRecord, targets: list[TargetRecord]) -> ChatBroadcastPhase:
    if record.status != "running":
        return record.status
    if record.rest_until_unix is not None and record.rest_until_unix > _seams.now():
        return "resting"
    if record.round <= 1 and any(
        t.state in _JOINING or (t.state == "queued" and t.member_account_id is None)
        for t in targets
    ):
        return "joining"
    return "running"


def _rows(
    record: CampaignRecord,
    targets: list[TargetRecord],
    journal: list[JournalRecord],
    events: list[EventRecord],
    planned: int | None,
) -> list[ChatBroadcastBoardRow]:
    by_chat: dict[str, list[ChatBroadcastHistoryEntry]] = defaultdict(list)
    delivered: dict[str, list[JournalRecord]] = defaultdict(list)
    for row in journal:
        if row.status == "pending":
            continue
        if row.status in _DELIVERED:
            delivered[row.chat_key].append(row)
        by_chat[row.chat_key].append(
            ChatBroadcastHistoryEntry(
                at=datetime.fromtimestamp(row.sent_unix or row.created_unix, UTC),
                account_id=row.account_id,
                round=row.round,
                kind=row.status,
                text=row.text,
                detail=row.error_type if row.status == "failed" else None,
            )
        )
    for event in events:
        by_chat[event.chat_key].append(
            ChatBroadcastHistoryEntry(
                at=datetime.fromtimestamp(event.at_unix, UTC),
                account_id=event.account_id,
                round=event.round,
                kind=event.kind,
                detail=event.detail,
            )
        )
    limit = settings.chat_broadcast.history_per_chat
    finished = record.status == "done"
    rows: list[ChatBroadcastBoardRow] = []
    for target in targets:
        if target.skip_reason == "removed":
            continue
        history = sorted(by_chat[target.chat_key], key=lambda entry: entry.at, reverse=True)
        sent = delivered[target.chat_key]
        last = sent[-1] if sent else None
        rows.append(
            ChatBroadcastBoardRow(
                chat_key=target.chat_key,
                raw=target.raw,
                title=target.title,
                kind=target.kind,
                account_id=target.assigned_account_id,
                handed_from=target.handed_from_account_id,
                state=target.state,
                skip_reason=target.skip_reason,
                round=target.round,
                sent_total=len(sent),
                planned_total=planned,
                next_action_at=_at(target.next_action_unix),
                requested_at=_at(target.requested_unix),
                last_text=None if last is None else last.text,
                last_sent_at=None if last is None else _at(last.sent_unix or last.created_unix),
                message_deleted=target.message_deleted,
                active=not finished and target.state not in {"done", "skipped"},
                history=history[:limit],
            )
        )
    return rows
