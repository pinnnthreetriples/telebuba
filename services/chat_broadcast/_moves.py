"""Chat moves shared by the workers, the coordinator and the board actions.

Every move is a compare-and-set on the states a chat may be leaving, so a manual skip
from the board is never overwritten by a worker that read the row a moment earlier,
and every move leaves a history entry the board shows.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Final

from core.logging import log_event
from core.repositories import chat_broadcast as repository
from services.chat_broadcast import _seams

if TYPE_CHECKING:
    from schemas.chat_broadcast_board import ChatBroadcastSkipReason
    from schemas.chat_broadcast_records import TargetRecord
    from services.chat_broadcast._context import RunContext

# A chat is still being worked in its round while it is in one of these.
ACTIVE_STATES: Final = (
    "queued",
    "joining",
    "captcha",
    "pending_approval",
    "waiting",
    "writing",
    "reconnecting",
    "waiting_account",
)
# States a new account can pick a chat up from without anything being in flight.
_HANDABLE: Final = tuple(state for state in ACTIVE_STATES if state not in {"joining", "captcha"})


class ChatBroadcastStoppedOnErrorError(RuntimeError):
    """A chat failed with "skip errors" off: the run stops so the operator can look."""


async def skip(
    ctx: RunContext,
    target: TargetRecord,
    reason: ChatBroadcastSkipReason,
    account_id: str | None,
) -> bool:
    moved = await repository.update_target(
        ctx.campaign_id,
        target.chat_key,
        expect_states=ACTIVE_STATES,
        state="skipped",
        skip_reason=reason,
        next_action_unix=None,
    )
    if moved:
        await repository.add_event(
            ctx.campaign_id,
            target.chat_key,
            round_number=target.round,
            kind="skipped",
            account_id=account_id,
            detail=reason,
        )
        await log_event(
            "WARNING",
            "chat_broadcast_chat_skipped",
            account_id=account_id,
            extra={"campaign_id": ctx.campaign_id, "chat": target.raw, "reason": reason},
        )
    return moved


async def defer(ctx: RunContext, target: TargetRecord, seconds: float, **fields: object) -> None:
    """Come back to this chat later; it keeps its account and its place in the chain."""
    await repository.update_target(
        ctx.campaign_id,
        target.chat_key,
        expect_states=ACTIVE_STATES,
        next_action_unix=_seams.now() + int(seconds),
        **fields,
    )


async def hand_over(
    ctx: RunContext,
    from_account: str,
    live: list[str],
    *,
    only_unjoined: bool = False,
) -> int:
    """Give ``from_account``'s unfinished chats to the live accounts, least loaded first.

    A chat the new account is not in starts over at the join — and at the pause after
    it — but keeps its place in the chain. With no live account left the chats wait for
    one. ``only_unjoined`` is the limit case: chats it already sits in stay with it.
    """
    targets = await repository.list_targets(ctx.campaign_id)
    others = [account for account in live if account != from_account]
    load = Counter(
        t.assigned_account_id for t in targets if t.state in ACTIVE_STATES and t.assigned_account_id
    )
    moved = 0
    for target in targets:
        if target.assigned_account_id != from_account or target.state not in _HANDABLE:
            continue
        if only_unjoined and target.member_account_id == from_account:
            continue
        if not others:
            await repository.update_target(
                ctx.campaign_id,
                target.chat_key,
                expect_states=_HANDABLE,
                state="waiting_account",
            )
            continue
        to = min(others, key=lambda account: (load[account], others.index(account)))
        if await assign(ctx, target, to):
            load[to] += 1
            moved += 1
    if moved:
        await log_event(
            "WARNING",
            "chat_broadcast_chats_handed",
            account_id=from_account,
            extra={"campaign_id": ctx.campaign_id, "count": moved},
        )
    return moved


async def assign(ctx: RunContext, target: TargetRecord, to: str) -> bool:
    """Move one chat to ``to``; it re-joins unless ``to`` is already inside."""
    inside = target.member_account_id == to
    fields: dict[str, object] = {
        "assigned_account_id": to,
        "handed_from_account_id": target.assigned_account_id,
    }
    if not inside:
        fields |= {"state": "queued", "next_action_unix": None, "requested_unix": None}
    elif target.state == "waiting_account":
        fields |= {"state": "waiting", "next_action_unix": _seams.now()}
    moved = await repository.update_target(
        ctx.campaign_id, target.chat_key, expect_states=_HANDABLE, **fields
    )
    if moved:
        await repository.add_event(
            ctx.campaign_id,
            target.chat_key,
            round_number=target.round,
            kind="handed",
            account_id=to,
            detail=target.assigned_account_id,
        )
    return moved


async def adopt_orphans(ctx: RunContext, live: list[str]) -> None:
    """Chats whose account is gone or halted go to the live accounts."""
    targets = await repository.list_targets(ctx.campaign_id)
    load = Counter(t.assigned_account_id for t in targets if t.state in ACTIVE_STATES)
    for target in targets:
        if target.state not in _HANDABLE or target.assigned_account_id in live or not live:
            continue
        to = min(live, key=lambda account: (load[account], live.index(account)))
        if await assign(ctx, target, to):
            load[to] += 1


async def halt_account(ctx: RunContext, account_id: str, reason: str) -> None:
    await repository.halt_account(ctx.campaign_id, account_id, reason)
    await log_event(
        "ERROR",
        "chat_broadcast_account_halted",
        account_id=account_id,
        extra={"campaign_id": ctx.campaign_id, "reason": reason},
    )


async def account_error(ctx: RunContext, account_id: str, *, failed: bool) -> bool:
    """Count an error in a row by this account; ``True`` when it must leave the run."""
    roster = {item.account_id: item for item in await repository.list_roster(ctx.campaign_id)}
    current = roster[account_id].consecutive_errors if account_id in roster else 0
    count = current + 1 if failed else 0
    if count != current:
        await repository.set_consecutive_errors(ctx.campaign_id, account_id, count)
    limit = ctx.settings.max_consecutive_errors
    return failed and limit > 0 and count >= limit


async def chat_failed(
    ctx: RunContext, account_id: str, target: TargetRecord, error_type: str | None
) -> bool:
    """A chat-level failure by this account. ``True`` when the account must leave."""
    await log_event(
        "WARNING",
        "chat_broadcast_message_failed",
        account_id=account_id,
        extra={"campaign_id": ctx.campaign_id, "chat": target.raw}
        | ({"error_type": error_type} if error_type else {}),
    )
    leave = await account_error(ctx, account_id, failed=True)
    if not ctx.settings.skip_errors:
        raise ChatBroadcastStoppedOnErrorError(error_type or "failed")
    await skip(ctx, target, "error", account_id)
    if leave:
        await halt_account(ctx, account_id, "errors_in_a_row")
    return leave
