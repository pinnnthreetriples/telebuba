"""One account's loop through its own queue of chats, for the whole run.

Accounts work in parallel, each through its own chats in order; a chat is only ever
touched by the account it is assigned to. Every chat has its own rounds: a chat that
finished its chain rests and opens its next round on its own, and the loop plays whatever
is due, the lowest round first (so a short rest cannot keep the chats behind it from their
turn). The loop re-reads its queue every pass, so a chat handed over (from a halted or
resting account, from the balance, or from the board) is picked up without a restart. It
lives until nothing is left anywhere in the campaign, so it can still take chats from an
account that drops out late. A rest ends the loop; the coordinator starts it again when
the account is back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal

from core.config import settings
from core.repositories import chat_broadcast as repository
from core.telegram_client import TelegramReadError
from schemas.telegram_action_results import ReadChatMessagesResult
from schemas.telegram_actions import ReadChatMessages
from services.chat_broadcast import _join, _moves, _schedule, _seams, _send

if TYPE_CHECKING:
    from collections.abc import Sequence

    from schemas.chat_broadcast_records import TargetRecord
    from services.chat_broadcast._context import RunContext

WorkerOutcome = Literal["finished", "halted", "resting", "volume"]
# Chats whose message ids belong to the chat, whoever reads them.
_GLOBAL_ID_KINDS: Final = frozenset({"megagroup", "channel"})


async def work(ctx: RunContext, account_id: str) -> WorkerOutcome:
    while True:
        _seams.assert_live_run()
        if await _send.volume_reached(ctx):
            return "volume"
        await _schedule.tick_safely(ctx, account_id=account_id)
        targets = await repository.list_targets(ctx.campaign_id)
        # Its own chats whose rest is over go on at once, without waiting for a tick.
        if await _schedule.promote(ctx, targets, owner=account_id):
            continue
        if _schedule.nothing_left(targets, ctx.rounds):
            return "finished"
        mine = [
            t
            for t in targets
            if t.assigned_account_id == account_id
            and t.state in _moves.ACTIVE_STATES
            and t.state != "waiting_account"
        ]
        now = _seams.now()
        # Lowest round first, then the run's own order: with a rest shorter than the pause
        # between chats the first chat would be due again before the last had its turn.
        due = sorted(
            (t for t in mine if (t.next_action_unix or 0) <= now),
            key=lambda t: (t.round, t.position),
        )
        if not due:
            await _seams.sleep(_idle(ctx, targets, account_id, now))
            continue
        step = await handle(ctx, account_id, due[0])
        if step == "halted" or step == "resting" or step == "volume":  # noqa: PLR1714 - narrows for ty
            return step
        # "idle" moved nothing worth a pause, but must still yield: a chat whose state
        # a board action changed under it is due again at once.
        await _seams.sleep(_send.between_chats(ctx) if step == "acted" else 1)


def _idle(ctx: RunContext, targets: Sequence[TargetRecord], account_id: str, now: int) -> float:
    """How long to sleep: to the nearest moment one of its chats is due, but not past the poll.

    A chat that is through for good (an error skip in the last round) is not coming back and
    is not waited for: its old time would keep the sleep at its one-second floor.
    """
    poll = settings.chat_broadcast.idle_poll_seconds
    upcoming = [
        t.next_action_unix
        for t in targets
        if t.assigned_account_id == account_id
        and t.next_action_unix is not None
        and (
            (t.state in _moves.ACTIVE_STATES and t.state != "waiting_account")
            or _schedule.will_return(t, ctx.rounds)
        )
    ]
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
        # A chat the operator kept still has its deletions recorded, but goes on.
        and not target.ignore_deleted
    ):
        await _moves.skip(ctx, target, "deleted", account_id)
        return "acted"
    return await _send.play_chain(ctx, account_id, target)


async def _was_deleted(ctx: RunContext, account_id: str, target: TargetRecord) -> bool:
    """Did an admin remove what we wrote here in the previous round?

    Read with the account that is in the chat now. The messages it can look up are its own,
    and — where message ids are the chat's, not the account's (a megagroup, a channel) —
    those another account sent before the chat was handed over. In a basic group ids are per
    account: another account's ids would name other messages and read as deleted, so a chat
    whose kind is not known to be global is read for this account's own messages only. An
    unreadable chat is not proof of a deletion.
    """
    journal = await repository.list_journal(ctx.campaign_id, ctx.run_id)
    shared_ids = target.chat_kind in _GLOBAL_ID_KINDS
    ours = [
        row
        for row in journal
        if row.chat_key == target.chat_key
        and row.round == target.round - 1
        and row.status == "sent"
        and row.tg_message_id is not None
        and (shared_ids or row.account_id == account_id)
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
