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
from services.chat_broadcast import _seams, _state

if TYPE_CHECKING:
    from collections.abc import Iterable

    from schemas.chat_broadcast import MinutesRange
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
# Chats that still count as work: the ones being played, and the ones resting between
# two of their own rounds.
OPEN_STATES: Final = (*ACTIVE_STATES, "round_done")
# States a new account can pick a chat up from without anything being in flight.
_HANDABLE: Final = tuple(state for state in ACTIVE_STATES if state not in {"joining", "captcha"})
# What the balance may move: nothing in flight (no join, captcha or send under way) and no
# join request, which belongs to the account that sent it. A resting chat moves too, so the
# account that wakes up finds work waiting.
REBALANCE_STATES: Final = ("queued", "waiting", "reconnecting", "waiting_account", "round_done")


class ChatBroadcastStoppedOnErrorError(RuntimeError):
    """A chat failed with "skip errors" off: the run stops so the operator can look."""


def open_load(targets: Iterable[TargetRecord]) -> Counter[str]:
    """Open chats per account — the one measure every placement and the balance use."""
    return Counter(
        t.assigned_account_id for t in targets if t.state in OPEN_STATES and t.assigned_account_id
    )


def chat_ref(target: TargetRecord) -> str:
    """How the log names a chat: its public handle, else its numeric id — never an invite link.

    An invite link is the way into a private chat, so it stays out of the logs that name a
    chat on every round.
    """
    if target.kind in {"public", "own"} and target.username:
        return f"@{target.username}"
    return f"id:{target.peer_id}" if target.peer_id else f"#{target.position}"


def receivers(accounts: Iterable[str], target: TargetRecord) -> list[str]:
    """The accounts that may be given ``target``: never one it banned or muted."""
    return [account for account in accounts if account not in target.refused_by]


def least_loaded(candidates: list[str], load: Counter[str]) -> str | None:
    """The idlest of ``candidates``, the first of them on a tie; ``None`` when there are none."""
    return min(candidates, key=lambda a: (load[a], candidates.index(a)), default=None)


def rest_end(started_unix: int, bounds: MinutesRange) -> int:
    """When a rest begun at ``started_unix`` ends, drawn from the operator's range."""
    return started_unix + int(_seams.rng.uniform(bounds.min, bounds.max) * 60)


def more_rounds(ctx: RunContext, target: TargetRecord) -> bool:
    """Does this chat have another round after the one it is in?"""
    return ctx.rounds is None or target.round < ctx.rounds


async def skip(
    ctx: RunContext,
    target: TargetRecord,
    reason: ChatBroadcastSkipReason,
    account_id: str | None,
) -> bool:
    """Take a chat out of the game. A chat an error skipped comes back after its rest."""
    started = _seams.now()
    retry = reason == "error" and more_rounds(ctx, target)
    until = rest_end(started, ctx.pace.rest_minutes) if retry else None
    moved = await repository.update_target(
        ctx.campaign_id,
        target.chat_key,
        expect_states=ACTIVE_STATES,
        state="skipped",
        skip_reason=reason,
        next_action_unix=until,
    )
    if moved and retry:
        _state.note_rest_started(ctx.campaign_id, target.chat_key, started)
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
            extra={"campaign_id": ctx.campaign_id, "chat": chat_ref(target), "reason": reason},
        )
    return moved


async def defer(
    ctx: RunContext, target: TargetRecord, seconds: float, *, state: str | None = None
) -> None:
    """Come back to this chat later; it keeps its account and its place in the chain."""
    until = _seams.now() + int(seconds)
    if state is None:
        await repository.update_target(
            ctx.campaign_id, target.chat_key, expect_states=ACTIVE_STATES, next_action_unix=until
        )
    else:
        await repository.update_target(
            ctx.campaign_id,
            target.chat_key,
            expect_states=ACTIVE_STATES,
            next_action_unix=until,
            state=state,
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
    it — but keeps its place in the chain. With no live account left, or none that the chat
    has not banned or muted, the chat waits for one. ``only_unjoined`` is the limit case:
    chats it already sits in stay with it.
    """
    targets = await repository.list_targets(ctx.campaign_id)
    others = [account for account in live if account != from_account]
    load = open_load(targets)
    moved = 0
    for target in targets:
        if target.assigned_account_id != from_account or target.state not in _HANDABLE:
            continue
        if only_unjoined and target.member_account_id == from_account:
            continue
        to = least_loaded(receivers(others, target), load)
        if to is None:
            await _wait_for_account(ctx, target)
            continue
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


async def _wait_for_account(ctx: RunContext, target: TargetRecord) -> None:
    """Nobody may take this chat now: it waits for an account (``reclaim_waiting`` returns it)."""
    if target.state != "waiting_account":
        await repository.update_target(
            ctx.campaign_id, target.chat_key, expect_states=_HANDABLE, state="waiting_account"
        )


async def assign(  # noqa: PLR0913 - the three modes of a move are keywords of one call
    ctx: RunContext,
    target: TargetRecord,
    to: str,
    *,
    expect_states: tuple[str, ...] = _HANDABLE,
    record_origin: bool = True,
    pin: bool = False,
) -> bool:
    """Move one chat to ``to``; it re-joins unless ``to`` is already inside.

    ``record_origin`` keeps who it came from on the chat — the FIRST account it was moved
    from, never overwritten by a later move: what decides that a chat has been moved once
    already. A balancing move leaves it alone — a chat that only changed hands to even the
    load is still owed its one move when a ban hits it. A chat resting between rounds
    changes owner and nothing else: its rest runs out as it was. ``pin`` is the operator's
    hand-over from the board: the balance leaves the chat where it was put; any other move
    ends the pin.
    """
    inside = target.member_account_id == to
    fields: dict[str, object] = {"assigned_account_id": to, "pinned": int(pin)}
    if record_origin and target.handed_from_account_id is None:
        fields["handed_from_account_id"] = target.assigned_account_id
    if target.state == "round_done":
        pass
    elif not inside:
        fields |= {"state": "queued", "next_action_unix": None, "requested_unix": None}
    elif target.state == "waiting_account":
        fields |= {"state": "waiting", "next_action_unix": _seams.now()}
    moved = await repository.update_target(
        ctx.campaign_id,
        target.chat_key,
        expect_states=expect_states,
        assigned_to=target.assigned_account_id,
        **fields,
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


async def adopt_orphans(ctx: RunContext, live: list[str], keep: list[str] | None = None) -> None:
    """Chats whose account is gone or halted go to the live accounts.

    ``keep`` are accounts sitting a rest out: their chats wait for them, not orphans.
    """
    owners = set(live) | set(keep or ())
    targets = await repository.list_targets(ctx.campaign_id)
    load = open_load(targets)
    for target in targets:
        if target.state not in _HANDABLE or target.assigned_account_id in owners or not live:
            continue
        to = least_loaded(receivers(live, target), load)
        if to is None:
            await _wait_for_account(ctx, target)
        elif await assign(ctx, target, to):
            load[to] += 1


async def reclaim_waiting(ctx: RunContext, live: list[str]) -> None:
    """An account is back: chats left waiting for one go back to work.

    A chat stays with its own account when that one is live, else goes to a live
    account already inside it, else to the least loaded one — never to an account that was
    banned or muted in it: with none left it keeps waiting.
    """
    if not live:
        return
    targets = await repository.list_targets(ctx.campaign_id)
    load = open_load(targets)
    for target in targets:
        if target.state != "waiting_account":
            continue
        if target.assigned_account_id in live:
            inside = target.member_account_id == target.assigned_account_id
            await repository.update_target(
                ctx.campaign_id,
                target.chat_key,
                expect_states=("waiting_account",),
                state="waiting" if inside else "queued",
                next_action_unix=_seams.now() if inside else None,
            )
            continue
        candidates = receivers(live, target)
        to = (
            target.member_account_id
            if target.member_account_id in candidates
            else least_loaded(candidates, load)
        )
        if to is not None and await assign(ctx, target, to):
            load[to] += 1


async def halt_account(ctx: RunContext, account_id: str, reason: str) -> None:
    await repository.halt_account(ctx.campaign_id, account_id, reason)
    _state.note_halt(ctx.campaign_id, account_id, _seams.now())
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
        extra={"campaign_id": ctx.campaign_id, "chat": chat_ref(target)}
        | ({"error_type": error_type} if error_type else {}),
    )
    leave = await account_error(ctx, account_id, failed=True)
    if not ctx.settings.skip_errors:
        raise ChatBroadcastStoppedOnErrorError(error_type or "failed")
    await skip(ctx, target, "error", account_id)
    if leave:
        await halt_account(ctx, account_id, "errors_in_a_row")
    return leave
