"""Getting one account into one chat: join, request, approval, captcha, the pause after.

The join budget is the fleet's shared one (``neurocomment_join_log``): Telegram counts
joins per ACCOUNT, whichever feature spends them. Reading it, joining and charging it
happen under ``services._join_lock`` — the same mutex neurocomment and neuroshilling take
— with the pacer in front of it, as in ``services.neuroshilling._telegram.join_target``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from core.channel_tokens import extract_invite_hash
from core.config import settings
from core.logging import log_event
from core.repositories import chat_broadcast as repository
from core.repositories.neurocomment import count_account_joins_since, record_join
from core.telegram_client import TelegramReadError
from schemas.telegram_action_results import ResolveChatResult
from schemas.telegram_actions import BroadcastJoinChatlist, JoinChannel, ResolveChat
from services import pacing
from services._account_limits import account_join_cap
from services._join_lock import join_lock
from services.chat_broadcast import _moves, _seams
from services.chat_broadcast._verdicts import JoinVerdict, classify_join, halt_reason

if TYPE_CHECKING:
    from schemas.chat_broadcast_records import TargetRecord
    from schemas.telegram_action_results import ActionResult
    from schemas.telegram_actions import TelegramAction
    from services.chat_broadcast._context import RunContext

Step = Literal["acted", "idle", "halted"]
_CAPTCHA_FAILED = frozenset({"give_up", "failed"})


def join_token(target: TargetRecord) -> str | None:
    """What a join or a resolve is made with: ``+HASH``, a username, or nothing."""
    if target.kind == "invite":
        invite = extract_invite_hash(target.raw)
        return f"+{invite}" if invite else None
    return target.username


def _join_action(target: TargetRecord) -> TelegramAction | None:
    if target.kind == "folder" and target.folder_slug and target.peer_id:
        return BroadcastJoinChatlist(slug=target.folder_slug, peer_id=target.peer_id)
    token = join_token(target)
    return None if token is None else JoinChannel(channel=token)


async def _at_join_cap(account_id: str) -> bool:
    cap = await account_join_cap(account_id, settings.chat_broadcast.max_joins_per_account_per_day)
    if cap <= 0:
        return False
    since = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    return await count_account_joins_since(account_id, since) >= cap


async def _cap_reached(ctx: RunContext, account_id: str, target: TargetRecord) -> Step:
    await log_event(
        "WARNING",
        "chat_broadcast_join_daily_cap",
        account_id=account_id,
        extra={"campaign_id": ctx.campaign_id, "chat": target.raw},
    )
    await _moves.defer(ctx, target, settings.chat_broadcast.limit_retry_seconds, state="queued")
    return "idle"


async def join(ctx: RunContext, account_id: str, target: TargetRecord) -> Step:
    """Walk ``account_id`` into ``target``; the chat leaves in its next state."""
    action = _join_action(target)
    if action is None:
        await _moves.skip(ctx, target, "unreachable", account_id)
        return "acted"
    inside = await _already_inside(account_id, target)
    if inside is not None:
        return await _enter_as_member(ctx, account_id, target, inside)
    if await _at_join_cap(account_id):
        return await _cap_reached(ctx, account_id, target)
    if not await repository.update_target(
        ctx.campaign_id,
        target.chat_key,
        expect_states=("queued", "reconnecting", "waiting"),
        state="joining",
    ):
        return "idle"
    await _seams.await_send_slot(f"join:{account_id}", join_pause())
    async with join_lock(account_id):
        if await _at_join_cap(account_id):
            return await _cap_reached(ctx, account_id, target)
        result = await _seams.execute(account_id, action)
        verdict = classify_join(result)
        if verdict in {"joined", "requested"}:
            await record_join(account_id)
    return await _on_verdict(ctx, account_id, target, verdict, result)


async def _already_inside(account_id: str, target: TargetRecord) -> ResolveChatResult | None:
    """The chat as this account sees it, when the account is already a member.

    Asked before joining because Telegram answers a re-join of a public chat with a
    plain success, not "already a participant": the join would be charged to the daily
    budget and followed by the operator's whole "write in a new chat after" pause. A
    folder join asks its own invite instead (``already_peers``).
    """
    if target.kind == "folder":
        return None
    token = join_token(target)
    resolved = None if token is None else await _resolve(account_id, token)
    return resolved if resolved is not None and resolved.member else None


async def _enter_as_member(
    ctx: RunContext, account_id: str, target: TargetRecord, inside: ResolveChatResult
) -> Step:
    """No join, no budget, no captcha and no pause: it can write right away."""
    if not await repository.update_target(
        ctx.campaign_id,
        target.chat_key,
        expect_states=("queued", "reconnecting", "waiting"),
        state="joining",
    ):
        return "idle"
    return await after_join(ctx, account_id, target, already=True, resolved=inside)


async def _on_verdict(
    ctx: RunContext,
    account_id: str,
    target: TargetRecord,
    verdict: JoinVerdict,
    result: ActionResult,
) -> Step:
    match verdict:
        case "joined" | "already":
            return await after_join(ctx, account_id, target, already=verdict == "already")
        case "requested":
            return await _requested(ctx, account_id, target)
        case "account_halt":
            await repository.update_target(
                ctx.campaign_id, target.chat_key, expect_states=("joining",), state="queued"
            )
            await _moves.halt_account(ctx, account_id, halt_reason(result))
            return "halted"
        case "retry":
            await _moves.defer(
                ctx, target, settings.chat_broadcast.reconnect_delay_seconds, state="reconnecting"
            )
            return "idle"
        case "invalid_link" | "banned":
            await _moves.skip(ctx, target, verdict, account_id)
            return "acted"
        case _:
            await repository.update_target(
                ctx.campaign_id, target.chat_key, expect_states=("joining",), state="queued"
            )
            fresh = await repository.fetch_target(ctx.campaign_id, target.chat_key)
            leave = await _moves.chat_failed(ctx, account_id, fresh or target, result.error_type)
            return "halted" if leave else "acted"


async def _requested(ctx: RunContext, account_id: str, target: TargetRecord) -> Step:
    now = _seams.now()
    await repository.update_target(
        ctx.campaign_id,
        target.chat_key,
        expect_states=("joining",),
        state="pending_approval",
        member_account_id=None,
        requested_unix=now,
        next_action_unix=now + int(settings.chat_broadcast.approval_poll_seconds),
    )
    await repository.add_event(
        ctx.campaign_id,
        target.chat_key,
        round_number=target.round,
        kind="requested",
        account_id=account_id,
    )
    await log_event(
        "INFO",
        "chat_broadcast_join_requested",
        account_id=account_id,
        extra={"campaign_id": ctx.campaign_id, "chat": target.raw},
    )
    return "acted"


async def poll_approval(ctx: RunContext, account_id: str, target: TargetRecord) -> Step:
    """Has the admin let us in? Past the operator's wait the chat is skipped."""
    now = _seams.now()
    waited = now - (target.requested_unix or now)
    if waited >= ctx.settings.approval_wait_hours * 3600:
        await _moves.skip(ctx, target, "not_approved", account_id)
        return "acted"
    token = join_token(target)
    if token is not None and await _resolve(account_id, token) is not None:
        await repository.add_event(
            ctx.campaign_id,
            target.chat_key,
            round_number=target.round,
            kind="approved",
            account_id=account_id,
        )
        await log_event(
            "INFO",
            "chat_broadcast_join_approved",
            account_id=account_id,
            extra={"campaign_id": ctx.campaign_id, "chat": target.raw},
        )
        if not await repository.update_target(
            ctx.campaign_id,
            target.chat_key,
            expect_states=("pending_approval",),
            state="joining",
        ):
            return "idle"
        return await after_join(ctx, account_id, target, already=False)
    await _moves.defer(ctx, target, settings.chat_broadcast.approval_poll_seconds)
    return "idle"


async def _resolve(account_id: str, token: str) -> ResolveChatResult | None:
    try:
        result = await _seams.execute_read(account_id, ResolveChat(target=token))
    except TelegramReadError:
        return None
    return result if isinstance(result, ResolveChatResult) else None


async def after_join(
    ctx: RunContext,
    account_id: str,
    target: TargetRecord,
    *,
    already: bool,
    resolved: ResolveChatResult | None = None,
) -> Step:
    """Inside: learn this account's id for the chat, pass a captcha, start the pause.

    Where the account already was it writes at once; a fresh join waits the operator's
    "write in a new chat after" first. ``resolved`` is a resolve already made for it.
    """
    peer_id = target.peer_id if target.kind in {"folder", "own"} else None
    token = join_token(target)
    if peer_id is None and token is not None:
        resolved = resolved or await _resolve(account_id, token)
        if resolved is not None and resolved.kind == "channel":
            await _moves.skip(ctx, target, "admin_only", account_id)
            return "acted"
        peer_id = None if resolved is None else resolved.chat_id
    if peer_id is None:
        await _moves.skip(ctx, target, "unreachable", account_id)
        return "acted"
    if not already:
        await repository.update_target(
            ctx.campaign_id, target.chat_key, expect_states=("joining",), state="captcha"
        )
        outcome = await _seams.solve_challenge(account_id, target.raw, peer_id)
        if outcome in _CAPTCHA_FAILED:
            await _moves.skip(ctx, target, "captcha", account_id)
            return "acted"
    now = _seams.now()
    due = now if already else now + ctx.settings.join_delay_minutes * 60
    moved = await repository.update_target(
        ctx.campaign_id,
        target.chat_key,
        expect_states=("joining", "captcha"),
        state="waiting",
        member_account_id=account_id,
        peer_id=peer_id,
        joined_unix=now,
        next_action_unix=due,
    )
    if moved:
        await repository.add_event(
            ctx.campaign_id,
            target.chat_key,
            round_number=target.round,
            kind="already_member" if already else "joined",
            account_id=account_id,
            detail=str(due),
        )
        await log_event(
            "INFO",
            "chat_broadcast_chat_joined",
            account_id=account_id,
            extra={"campaign_id": ctx.campaign_id, "chat": target.raw, "already": already},
        )
    return "acted"


def join_pause() -> float:
    """A jittered spacing between joins of one account, like neurocomment's."""
    limits = settings.chat_broadcast
    return pacing.human_delay(
        limits.join_gap_seconds,
        limits.join_gap_seconds * 2,
        rng=_seams.rng,
        mu=limits.delay_lognorm_mu,
        sigma=limits.delay_lognorm_sigma,
    )
