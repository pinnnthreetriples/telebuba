"""One account's loop through its own queue of chats, for one round.

Accounts work in parallel, each through its own chats in order; a chat is only ever
touched by the account it is assigned to. The loop re-reads its queue every pass, so a
chat handed over (from a halted account, or from the board) is picked up without a
restart, and it stays alive until the whole round is finished — not just its own part —
so it can still take chats from an account that drops out late.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from core.config import settings
from core.repositories import chat_broadcast as repository
from core.telegram_client import TelegramReadError
from schemas.telegram_action_results import ReadChatMessagesResult
from schemas.telegram_actions import ReadChatMessages
from services.chat_broadcast import _join, _moves, _seams, _send

if TYPE_CHECKING:
    from schemas.chat_broadcast_records import TargetRecord
    from services.chat_broadcast._context import RunContext

WorkerOutcome = Literal["round_done", "halted", "volume"]


async def work(ctx: RunContext, account_id: str, round_number: int) -> WorkerOutcome:
    while True:
        _seams.assert_live_run()
        if await _send.volume_reached(ctx):
            return "volume"
        targets = await repository.list_targets(ctx.campaign_id)
        open_chats = [
            t for t in targets if t.state in _moves.ACTIVE_STATES and t.round == round_number
        ]
        if not open_chats:
            return "round_done"
        mine = [
            t
            for t in open_chats
            if t.assigned_account_id == account_id and t.state != "waiting_account"
        ]
        now = _seams.now()
        due = [t for t in mine if (t.next_action_unix or 0) <= now]
        if not due:
            await _seams.sleep(_idle(mine, now))
            continue
        step = await handle(ctx, account_id, due[0])
        if step == "halted":
            return "halted"
        if step == "volume":
            return "volume"
        # "idle" moved nothing worth a pause, but must still yield: a chat whose state
        # a board action changed under it is due again at once.
        await _seams.sleep(_send.between_chats(ctx) if step == "acted" else 1)


def _idle(mine: list[TargetRecord], now: int) -> float:
    poll = settings.chat_broadcast.idle_poll_seconds
    upcoming = [t.next_action_unix for t in mine if t.next_action_unix is not None]
    if not upcoming:
        return poll
    return float(min(poll, max(1, min(upcoming) - now)))


async def handle(ctx: RunContext, account_id: str, target: TargetRecord) -> _send.Step:
    """Take one due chat one step further."""
    if (
        ctx.settings.skip_already_written
        and target.round == 1
        and target.step_index == 0
        and target.state == "queued"
        and await repository.written_before(target.chat_key, target.peer_id, run_id=ctx.run_id)
    ):
        await _moves.skip(ctx, target, "already_written", account_id)
        return "idle"
    if target.state == "pending_approval":
        return await _join.poll_approval(ctx, account_id, target)
    if target.member_account_id != account_id:
        if await _send.at_account_limit(ctx, account_id):
            # No point joining a chat it could not write to for an hour.
            return await _send.limit_reached(ctx, account_id, target, state=target.state)
        return await _join.join(ctx, account_id, target)
    if (
        target.round > 1
        and target.step_index == 0
        and ctx.settings.skip_deleted
        and await _was_deleted(ctx, account_id, target)
    ):
        await _moves.skip(ctx, target, "deleted", account_id)
        return "acted"
    return await _send.play_chain(ctx, account_id, target)


async def _was_deleted(ctx: RunContext, account_id: str, target: TargetRecord) -> bool:
    """Did an admin remove what we wrote here in the previous round?

    Read with the account that sent them; message ids of a basic group are that
    account's own. An unreadable chat is not proof of a deletion.
    """
    journal = await repository.list_journal(ctx.campaign_id, ctx.run_id)
    ours = [
        row
        for row in journal
        if row.chat_key == target.chat_key
        and row.round == target.round - 1
        and row.status == "sent"
        and row.tg_message_id is not None
        and row.account_id == account_id
    ]
    if not ours or target.peer_id is None:
        return False
    try:
        result = await _seams.execute_read(
            account_id,
            ReadChatMessages(
                chat=str(target.peer_id),
                message_ids=[row.tg_message_id for row in ours if row.tg_message_id],
            ),
        )
    except TelegramReadError:
        return False
    if not isinstance(result, ReadChatMessagesResult) or not result.missing_ids:
        return False
    missing = set(result.missing_ids)
    await repository.mark_deleted([row.id for row in ours if row.tg_message_id in missing])
    await repository.update_target(ctx.campaign_id, target.chat_key, message_deleted=1)
    await repository.add_event(
        ctx.campaign_id,
        target.chat_key,
        round_number=target.round,
        kind="deleted",
        account_id=account_id,
    )
    return True
