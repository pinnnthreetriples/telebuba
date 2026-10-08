"""Playing the chain into one chat: limits, journal, rewrite, typing, send, verdict.

Each step is reserved in the journal (``pending``) BEFORE the send. An occupied key is a
step already played — the resume path — and a refusal that never reached the chat
(slow mode, a dropped connection, not a member) gives the reservation back so the step
is played later instead of being lost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, NamedTuple

from core.config import settings
from core.logging import log_event
from core.repositories import chat_broadcast as repository
from schemas.chat_broadcast_records import JournalKey
from schemas.telegram_actions import BroadcastForwardPost, BroadcastSendMessage
from services import pacing
from services.chat_broadcast import _moves, _rewrite, _seams
from services.chat_broadcast._verdicts import classify_send, halt_reason
from services.chat_broadcast.links import parse_post

if TYPE_CHECKING:
    from schemas.chat_broadcast import ChatBroadcastMessage
    from schemas.chat_broadcast_records import ChatBroadcastJournalKind, TargetRecord
    from schemas.telegram_actions import ActionResult, TelegramAction
    from services.chat_broadcast._context import RunContext

Step = Literal["acted", "idle", "halted", "volume"]
_WRITABLE = ("queued", "waiting", "writing", "reconnecting")
_HOUR = 3600
_DAY = 86_400


async def volume_reached(ctx: RunContext) -> bool:
    """The operator's "stop after N messages / N hours"."""
    if ctx.settings.stop_mode == "time":
        return _seams.now() >= ctx.started_unix + ctx.settings.stop_hours * _HOUR
    return await repository.count_run_sent(ctx.run_id) >= ctx.settings.stop_messages


async def at_account_limit(ctx: RunContext, account_id: str) -> bool:
    if not ctx.settings.account_limit:
        return False
    now = _seams.now()
    hour = await repository.count_account_sends_since(account_id, now - _HOUR)
    if hour >= ctx.settings.per_hour:
        return True
    return (
        await repository.count_account_sends_since(account_id, now - _DAY) >= ctx.settings.per_day
    )


def _pause(bounds: tuple[int, int]) -> float:
    limits = settings.chat_broadcast
    return pacing.human_delay(
        float(bounds[0]),
        float(bounds[1]),
        rng=_seams.rng,
        mu=limits.delay_lognorm_mu,
        sigma=limits.delay_lognorm_sigma,
    )


def between_chats(ctx: RunContext) -> float:
    return _pause((ctx.settings.between_chats.min, ctx.settings.between_chats.max))


def _typing_seconds(ctx: RunContext, text: str) -> float:
    if not ctx.settings.typing or not text:
        return 0.0
    limits = settings.chat_broadcast
    seconds = len(text) / limits.typing_chars_per_second
    return min(max(seconds, limits.typing_min_seconds), limits.typing_max_seconds)


def _kind(message: ChatBroadcastMessage) -> ChatBroadcastJournalKind:
    if message.kind == "post":
        return "post"
    return "photo" if message.photo is not None else "text"


async def _action(
    ctx: RunContext, step: int, message: ChatBroadcastMessage, target: TargetRecord
) -> tuple[TelegramAction | None, str, bool]:
    """The write for one step, with the text that goes into the journal."""
    chat = str(target.peer_id) if target.peer_id else (target.username or target.raw)
    if message.kind == "post":
        post = parse_post(message.post)
        if post is None:
            return None, message.post, False
        return (
            BroadcastForwardPost(chat=chat, channel=post[0], message_id=post[1]),
            message.post,
            False,
        )
    text, rewritten = await _rewrite.compose(ctx, step, message, target)
    photo = ctx.photos.get(message.photo.media_id) if message.photo is not None else None
    if not text.strip() and photo is None:
        return None, text, rewritten
    action = BroadcastSendMessage(
        chat=chat,
        text=text,
        photo=photo,
        photo_name=message.photo.name if message.photo is not None else "photo.jpg",
        typing_seconds=_typing_seconds(ctx, text),
    )
    return action, text, rewritten


async def _pause_before_next(ctx: RunContext, target: TargetRecord) -> None:
    """Wait out the pause between two messages, with the next send time on the board."""
    pause = _pause((ctx.settings.between_messages.min, ctx.settings.between_messages.max))
    await repository.update_target(
        ctx.campaign_id,
        target.chat_key,
        expect_states=("writing",),
        next_action_unix=_seams.now() + int(pause),
    )
    try:
        await _seams.sleep(pause)
    finally:
        # Also when the run is cancelled mid-pause: a stale future time would hold the chat.
        await repository.update_target(
            ctx.campaign_id, target.chat_key, expect_states=("writing",), next_action_unix=None
        )


async def play_chain(ctx: RunContext, account_id: str, target: TargetRecord) -> Step:
    """Send the rest of the chain into ``target`` from its current step."""
    if not await repository.update_target(
        ctx.campaign_id, target.chat_key, expect_states=_WRITABLE, state="writing"
    ):
        return "idle"
    for step in range(target.step_index, len(ctx.chain)):
        fresh = await repository.fetch_target(ctx.campaign_id, target.chat_key)
        if fresh is None or fresh.state != "writing" or fresh.assigned_account_id != account_id:
            return "acted"
        if await volume_reached(ctx):
            return "volume"
        if await at_account_limit(ctx, account_id):
            return await limit_reached(ctx, account_id, fresh, state="waiting")
        outcome = await _play_step(ctx, account_id, fresh, step)
        if outcome is not None:
            return outcome
        if step < len(ctx.chain) - 1:
            await _pause_before_next(ctx, target)
    await repository.update_target(
        ctx.campaign_id,
        target.chat_key,
        expect_states=("writing",),
        state="round_done",
        next_action_unix=None,
    )
    return "acted"


async def limit_reached(
    ctx: RunContext, account_id: str, target: TargetRecord, *, state: str
) -> Step:
    """Its limit is spent: the chats it has not entered go to accounts with room.

    The rest wait for its window to reopen; with nobody having room, everything waits.
    """
    await log_event(
        "WARNING",
        "chat_broadcast_account_limit",
        account_id=account_id,
        extra={"campaign_id": ctx.campaign_id},
    )
    await _moves.defer(ctx, target, settings.chat_broadcast.limit_retry_seconds, state=state)
    roster = await repository.list_roster(ctx.campaign_id)
    room = [
        item.account_id
        for item in roster
        if item.state == "active"
        and item.account_id != account_id
        and not await at_account_limit(ctx, item.account_id)
    ]
    if room:
        await _moves.hand_over(ctx, account_id, room, only_unjoined=True)
    return "idle"


async def _play_step(
    ctx: RunContext, account_id: str, target: TargetRecord, step: int
) -> Step | None:
    """One message. ``None`` = sent (or already played), go on with the chain."""
    message = ctx.chain[step]
    key = JournalKey(
        run_id=ctx.run_id, chat_key=target.chat_key, round=target.round, step_index=step
    )
    claimed = await repository.claim_message(
        key,
        campaign_id=ctx.campaign_id,
        account_id=account_id,
        kind=_kind(message),
        peer_id=target.peer_id,
    )
    if not claimed:
        await _advance(ctx, target, step)
        return None
    action, text, rewritten = await _action(ctx, step, message, target)
    if action is None:
        await repository.settle_message(key, "failed", text=text, error_type="EmptyMessage")
        await _advance(ctx, target, step)
        return None
    result = await _seams.execute(account_id, action)
    return await _settle(ctx, account_id, target, result, _Outgoing(key, text, rewritten))


async def _advance(ctx: RunContext, target: TargetRecord, step: int) -> None:
    await repository.update_target(
        ctx.campaign_id, target.chat_key, expect_states=("writing",), step_index=step + 1
    )


class _Outgoing(NamedTuple):
    """One step as it went out: its journal key and its text."""

    key: JournalKey
    text: str
    rewritten: bool


async def _settle(
    ctx: RunContext,
    account_id: str,
    target: TargetRecord,
    result: ActionResult,
    outgoing: _Outgoing,
) -> Step | None:
    """Journal, account and chat after one send."""
    key, text, rewritten = outgoing
    verdict = classify_send(result)
    if verdict in {"sent", "unconfirmed"}:
        await repository.settle_message(
            key,
            "sent" if verdict == "sent" else "unconfirmed",
            text=text,
            rewritten=rewritten,
            tg_message_id=result.message_id,
            error_type=result.error_type,
        )
        await _moves.account_error(ctx, account_id, failed=False)
        await _advance(ctx, target, key.step_index)
        await log_event(
            "INFO",
            "chat_broadcast_message_sent",
            account_id=account_id,
            extra={
                "campaign_id": ctx.campaign_id,
                "chat": target.raw,
                "step": key.step_index + 1,
                "steps": len(ctx.chain),
                "round": key.round,
                "rewritten": rewritten,
            },
        )
        return None
    if verdict in {"chat_wait", "reconnect", "not_member"}:
        await repository.release_message(key)
        return await _retry_later(ctx, account_id, target, verdict, result)
    if verdict == "account_halt":
        # Nothing reached the chat: the step goes back, for whoever takes the chat over.
        await repository.release_message(key)
        await repository.update_target(
            ctx.campaign_id, target.chat_key, expect_states=("writing",), state="waiting"
        )
        await _moves.halt_account(ctx, account_id, halt_reason(result))
        return "halted"
    if verdict == "failed" and not ctx.settings.skip_errors:
        # The run stops for the operator to look; Continue must send this step again.
        await repository.release_message(key)
    else:
        await repository.settle_message(key, "failed", text=text, error_type=result.error_type)
    if verdict == "admin_only" or verdict == "banned":  # noqa: PLR1714 - narrows for ty
        await _moves.skip(ctx, target, verdict, account_id)
        return "acted"
    leave = await _moves.chat_failed(ctx, account_id, target, result.error_type)
    return "halted" if leave else "acted"


async def _retry_later(
    ctx: RunContext, account_id: str, target: TargetRecord, verdict: str, result: ActionResult
) -> Step:
    if verdict == "chat_wait":
        seconds = result.flood_wait_seconds or settings.chat_broadcast.reconnect_delay_seconds
        await _moves.defer(ctx, target, seconds, state="waiting")
        return "acted"
    if verdict == "reconnect":
        await _moves.defer(
            ctx, target, settings.chat_broadcast.reconnect_delay_seconds, state="reconnecting"
        )
        await repository.add_event(
            ctx.campaign_id,
            target.chat_key,
            round_number=target.round,
            kind="reconnecting",
            account_id=account_id,
        )
        return "idle"
    # Not a member: joined this run and still refused — the chat will not take us.
    if target.joined_unix is not None and target.member_account_id == account_id:
        await _moves.skip(ctx, target, "unreachable", account_id)
        return "acted"
    await repository.update_target(
        ctx.campaign_id,
        target.chat_key,
        expect_states=("writing",),
        state="queued",
        member_account_id=None,
    )
    return "acted"
