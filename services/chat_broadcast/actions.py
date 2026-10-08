"""Manual actions from the board: write now, hand over, skip, keep a chat despite deletions.

Each is a compare-and-set on the state the chat must be in, so a click that lands after
the engine moved the chat on answers 409 ``target_state_changed`` instead of undoing
what the engine did.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.logging import log_event
from core.repositories import chat_broadcast as repository
from services.chat_broadcast import _coordinator, _moves, _seams
from services.chat_broadcast._context import RunContext
from services.chat_broadcast._errors import (
    ACCOUNT_NOT_IN_CAMPAIGN,
    TARGET_NOT_FOUND,
    TARGET_STATE_CHANGED,
    ChatBroadcastConflictError,
    ChatBroadcastInvalidError,
)
from services.chat_broadcast.campaigns import settings_of

if TYPE_CHECKING:
    from schemas.chat_broadcast import ChatBroadcastTargetAction
    from schemas.chat_broadcast_records import CampaignRecord, TargetRecord

# A chat being joined or passing a captcha has a call in flight; moving it now would
# race that call.
_HANDABLE = ("queued", "pending_approval", "waiting", "writing", "reconnecting", "waiting_account")
_RESUMABLE = ("stopped", "stalled", "failed")
# A keep decides from a read; when the run moved before the write, it reads and decides
# again — the run crosses a boundary in milliseconds, so a few tries are plenty.
_KEEP_ATTEMPTS = 3


async def act_on_target(campaign_id: str, request: ChatBroadcastTargetAction) -> bool:
    """Apply one board action. ``False`` means no such campaign."""
    record = await repository.fetch_campaign(campaign_id)
    if record is None:
        return False
    target = await repository.fetch_target(campaign_id, request.chat_key)
    if target is None:
        raise ChatBroadcastConflictError(TARGET_NOT_FOUND)
    ctx = RunContext(
        campaign_id=campaign_id,
        run_id=record.run_id or "",
        settings=settings_of(record),
        chain=[],
    )
    if request.action == "now":
        moved = target.state == "waiting" and await repository.update_target(
            campaign_id,
            target.chat_key,
            expect_states=("waiting",),
            next_action_unix=_seams.now(),
        )
    elif request.action == "skip":
        moved = await _moves.skip(ctx, target, "manual", None)
    elif request.action == "keep":
        moved = await _keep(ctx, record, target)
    else:
        moved = await _hand(ctx, campaign_id, target, request.account_id)
    if not moved:
        raise ChatBroadcastConflictError(TARGET_STATE_CHANGED)
    return True


async def _hand(
    ctx: RunContext, campaign_id: str, target: TargetRecord, account_id: str | None
) -> bool:
    roster = {item.account_id: item for item in await repository.list_roster(campaign_id)}
    entry = roster.get(account_id or "")
    if entry is None or entry.state != "active":
        raise ChatBroadcastInvalidError(ACCOUNT_NOT_IN_CAMPAIGN)
    if target.state not in _HANDABLE or target.assigned_account_id == entry.account_id:
        return False
    return await _moves.assign(ctx, target, entry.account_id)


async def _keep(ctx: RunContext, record: CampaignRecord, target: TargetRecord) -> bool:
    """Back to work a chat skipped for deleted messages; deletions no longer skip it.

    The deletions are an admin's signal the operator may overrule; a ban still stops the
    chat. It keeps its account (still inside) unless that account left the run.

    Decided from what was read, written only while the campaign is still there: the run
    may close the round, open the next one or finish in between — then it decides anew.
    """
    into_open_round = False
    for _attempt in range(_KEEP_ATTEMPTS):
        if target.state != "skipped" or target.skip_reason != "deleted":
            return False
        fields = await _comeback(record, target)
        into_open_round = bool(fields) and record.status == "running"
        if await repository.keep_target(
            record,
            target.chat_key,
            open_states=_moves.ACTIVE_STATES if into_open_round else None,
            **fields,
        ):
            break
        fresh = await repository.fetch_campaign(ctx.campaign_id)
        moved = await repository.fetch_target(ctx.campaign_id, target.chat_key)
        if fresh is None or moved is None:
            return False
        record, target = fresh, moved
    else:
        return False
    if into_open_round:
        # Its account may have dropped out since; a live one takes the chat, as at the
        # start of every round.
        live = await _coordinator.live_accounts(ctx.campaign_id)
        if live and target.assigned_account_id not in live:
            await _moves.adopt_orphans(ctx, live)
    await log_event(
        "INFO",
        "chat_broadcast_chat_kept",
        extra={"campaign_id": ctx.campaign_id, "chat": target.raw},
    )
    return True


async def _comeback(record: CampaignRecord, target: TargetRecord) -> dict[str, object]:
    """Where a kept chat goes, by where the campaign is.

    * a round under way: queued in it, due now — the workers pick it up this round;
    * stopped, stalled or failed: queued in the round it stopped in, which Continue
      plays again;
    * anything else — a rest, a round whose chats are all through (it may be closing),
      a run stopping or finished: it stays skipped and only takes the flag. The next
      round re-queues it; the next run lays its chats out anew and carries the flag over.
    """
    if record.round == 0:
        return {}
    requeue: dict[str, object] = {
        "state": "queued",
        "skip_reason": None,
        "round": record.round,
        "step_index": 0,
    }
    if record.status in _RESUMABLE:
        return requeue | {"next_action_unix": None}
    if (
        record.status == "running"
        and record.rest_until_unix is None
        and await _round_open(record, target)
    ):
        return requeue | {"next_action_unix": _seams.now()}
    return {}


async def _round_open(record: CampaignRecord, target: TargetRecord) -> bool:
    return any(
        t.state in _moves.ACTIVE_STATES and t.round == record.round
        for t in await repository.list_targets(record.campaign_id)
        if t.chat_key != target.chat_key
    )
