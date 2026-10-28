"""Smart rotation: an account that is stopped rests and comes back; only a dead one leaves.

What stops an account, and what becomes of it:

* **A flood wait** (Telegram's, or the premium one) — it rests for the time Telegram
  named plus a margin, then works on. **Its own hourly/daily limit** — it rests until
  the oldest send in the window falls out of it, to the second, so the board never
  promises a send that will not happen.
* **A spam block** (PeerFlood) — it rests for hours and @SpamBot is asked at the end; a
  block still standing takes it out. A ban that arrives as ``UserBannedInChannelError``
  is either that block or a ban in this one chat, and @SpamBot is asked at once: a clean
  account only lost this chat, which goes to another account.
* **A mute in one chat** — ``ChatWriteForbiddenError`` cannot tell a chat closed to all
  from an admin muting just this account; the chat's rights can. A mute on this account
  alone moves the chat to another one; a chat closed to everybody is skipped.
* **A dead session** (frozen, deactivated, logged out) — it leaves for good, as before.

While it rests its chats are not abandoned: a short rest keeps them, a medium one hands
over the chats it has not joined (re-joining costs a join and a pause, waiting does not),
a long one hands over everything — always to accounts that still have room. A round with
every account resting waits for the first one back instead of stalling.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Final, Literal

from core.config import settings
from core.logging import log_event
from core.repositories import chat_broadcast as repository
from core.telegram_client import TelegramReadError
from schemas.telegram_actions_rights import CheckWriteRights, WriteRightsResult
from services import _account_owner
from services.chat_broadcast import _moves, _seams
from services.chat_broadcast._verdicts import halt_reason

if TYPE_CHECKING:
    from schemas.chat_broadcast_board import ChatBroadcastSkipReason
    from schemas.chat_broadcast_records import RosterAccount, TargetRecord
    from schemas.telegram_actions import ActionResult
    from services.chat_broadcast._context import RunContext

_OWNER = "chat_broadcast"
_HOUR = 3600
_DAY = 86_400
_BANNED_IN_CHAT: Final = "UserBannedInChannelError"
# How a chat refuses a muted member — and a chat closed to all members alike.
_MUTE_ERRORS: Final = frozenset({"ChatWriteForbiddenError", "ChatRestrictedError"})
SPAM_REASONS: Final = frozenset({"peer_flood", _BANNED_IN_CHAT})
LIMIT_REASONS: Final = frozenset({"hour_limit", "day_limit"})
# Halts a new Start or Continue does not undo: the account itself is gone or full. Any
# other halt (errors in a row, a spam block still standing, a pre-rotation flood) gets
# another chance — the operator pressing Start is the decision to try it again.
FINAL_HALT_REASONS: Final = frozenset(
    {"account_deactivated", "account_frozen", "session_dead", "ChannelsTooMuchError"}
)

Resting = Literal["resting"]


def _held(item: RosterAccount, campaign_id: str) -> bool:
    return (
        item.state == "active"
        and _account_owner.owner_of(item.account_id) == _OWNER
        and _account_owner.holder_of(item.account_id) == campaign_id
    )


async def live_accounts(campaign_id: str) -> list[str]:
    """Roster accounts in the run, held by it and not resting, in roster order."""
    roster = await repository.list_roster(campaign_id)
    return [
        item.account_id
        for item in roster
        if _held(item, campaign_id) and item.resting_until_unix is None
    ]


async def resting_accounts(campaign_id: str) -> list[str]:
    """Roster accounts held by the run that are sitting a rest out."""
    roster = await repository.list_roster(campaign_id)
    return [
        item.account_id
        for item in roster
        if _held(item, campaign_id) and item.resting_until_unix is not None
    ]


async def limit_ends(ctx: RunContext, account_id: str) -> tuple[int, str] | None:
    """When the account's hourly/daily limit lets it write again; ``None`` = it has room.

    The windows roll, so the moment is the oldest send that has to fall out of the
    window before one more fits in it.
    """
    if not ctx.settings.account_limit:
        return None
    now = _seams.now()
    ends: list[tuple[int, str]] = []
    for window, cap, reason in (
        (_HOUR, ctx.settings.per_hour, "hour_limit"),
        (_DAY, ctx.settings.per_day, "day_limit"),
    ):
        times = await repository.list_account_send_times_since(account_id, now - window)
        if len(times) >= cap:
            ends.append((max(now + 1, times[len(times) - cap] + window), reason))
    return max(ends) if ends else None


async def room(ctx: RunContext, exclude: str) -> list[str]:
    """Live accounts other than ``exclude`` that may still write now."""
    return [
        account
        for account in await live_accounts(ctx.campaign_id)
        if account != exclude and await limit_ends(ctx, account) is None
    ]


async def rest_for(
    ctx: RunContext,
    account_id: str,
    seconds: float,
    reason: str,
    target: TargetRecord | None = None,
) -> Resting:
    """Rest for Telegram's ``seconds`` plus the margin."""
    until = _seams.now() + int(seconds + settings.chat_broadcast.rest_margin_seconds)
    return await rest_until(ctx, account_id, until, reason, target)


async def rest_until(
    ctx: RunContext,
    account_id: str,
    until: int,
    reason: str,
    target: TargetRecord | None = None,
) -> Resting:
    """Take the account out of the rotation until ``until``; place its chats.

    ``target`` is the chat it was refused in: it comes back to it then — unless the
    chat is handed over with the rest.
    """
    await repository.rest_account(ctx.campaign_id, account_id, until, reason)
    if target is not None:
        await repository.update_target(
            ctx.campaign_id,
            target.chat_key,
            expect_states=_moves.ACTIVE_STATES,
            next_action_unix=until,
        )
    handed = await _place_chats(ctx, account_id, until - _seams.now())
    await log_event(
        "WARNING",
        "chat_broadcast_account_resting",
        account_id=account_id,
        extra={"campaign_id": ctx.campaign_id, "reason": reason, "until": until, "count": handed},
    )
    return "resting"


async def _place_chats(ctx: RunContext, account_id: str, seconds: int) -> int:
    limits = settings.chat_broadcast
    if seconds <= limits.keep_chats_rest_seconds:
        return 0
    others = await room(ctx, account_id)
    if not others:
        # Nobody can take them now: they stay with it and it picks them up when back.
        return 0
    return await _moves.hand_over(
        ctx, account_id, others, only_unjoined=seconds <= limits.handover_rest_seconds
    )


async def account_refused(
    ctx: RunContext,
    account_id: str,
    target: TargetRecord,
    verdict: Literal["account_rest", "account_spam", "account_halt"],
    result: ActionResult,
) -> Literal["resting", "acted", "halted"]:
    """Telegram refused the ACCOUNT, not the chat: rest it, move the chat, or let it go.

    A flood rests it for Telegram's wait. A spam block rests it for hours — unless the
    refusal was a ban @SpamBot says the account does not carry, which is this chat's
    ban: only the chat moves. A dead session leaves.
    """
    reason = halt_reason(result)
    limits = settings.chat_broadcast
    if verdict == "account_rest":
        seconds = result.flood_wait_seconds or limits.default_flood_seconds
        return await rest_for(ctx, account_id, seconds, reason, target)
    if verdict == "account_halt":
        await _moves.halt_account(ctx, account_id, reason)
        return "halted"
    if (
        reason == _BANNED_IN_CHAT
        and target.handed_from_account_id is None
        and await _seams.spam_verdict(account_id) == "clean"
    ):
        await reroute(ctx, account_id, target, "banned")
        return "acted"
    return await rest_for(ctx, account_id, limits.spam_rest_seconds, reason, target)


async def muted_alone(account_id: str, target: TargetRecord, result: ActionResult) -> bool:
    """Did an admin mute this one account, with the chat open to everybody else?

    Asked only of a chat not moved yet: a chat that mutes its second account too is a
    chat that does not want us. A read that fails is no answer, and the chat is skipped
    as before.
    """
    if result.error_type not in _MUTE_ERRORS or target.handed_from_account_id is not None:
        return False
    chat = str(target.peer_id) if target.peer_id else (target.username or target.raw)
    try:
        rights = await _seams.execute_read(account_id, CheckWriteRights(channel=chat, in_chat=True))
    except TelegramReadError:
        return False
    return isinstance(rights, WriteRightsResult) and rights.scope == "self_only"


async def reroute(
    ctx: RunContext,
    account_id: str,
    target: TargetRecord,
    reason: ChatBroadcastSkipReason,
) -> None:
    """This chat banned or muted this account; another one writes there.

    Moved once at most: a chat that refuses its second account too is a chat that
    refuses us, and with nobody to move it to it is skipped for ``reason``.
    """
    others = await room(ctx, account_id) or [
        account for account in await live_accounts(ctx.campaign_id) if account != account_id
    ]
    fresh = await repository.fetch_target(ctx.campaign_id, target.chat_key) or target
    if not others:
        await _moves.skip(ctx, fresh, reason, account_id)
        return
    load = Counter(
        t.assigned_account_id
        for t in await repository.list_targets(ctx.campaign_id)
        if t.state in _moves.ACTIVE_STATES
    )
    to = min(others, key=lambda account: (load[account], others.index(account)))
    if await _moves.assign(ctx, fresh, to):
        await log_event(
            "WARNING",
            "chat_broadcast_chat_rerouted"
            if reason == "banned"
            else "chat_broadcast_muted_rerouted",
            account_id=account_id,
            extra={"campaign_id": ctx.campaign_id, "chat": target.raw, "to": to},
        )


async def limit(ctx: RunContext, account_id: str, target: TargetRecord) -> Resting | None:
    """Rest until its limit lets it write again; ``None`` when it has room after all."""
    ends = await limit_ends(ctx, account_id)
    if ends is None:
        return None
    until, reason = ends
    return await rest_until(ctx, account_id, until, reason, target)


async def sit_out(ctx: RunContext, account_id: str) -> bool:
    """Wait the account's rest out. ``True`` = back in the run, ``False`` = it left.

    Slept in slices, re-reading the end each time, like the rest between rounds. At the
    end a spam block is checked with @SpamBot and a limit is counted again — a send by
    another campaign may have used the room up meanwhile.
    """
    tick = settings.chat_broadcast.rest_poll_seconds
    while True:
        item = await _roster_item(ctx, account_id)
        if item is None or item.state != "active":
            return False
        if item.resting_until_unix is None:
            return True
        if (remaining := item.resting_until_unix - _seams.now()) > 0:
            await _seams.sleep(min(remaining, tick))
            continue
        _seams.assert_live_run()
        if item.rest_reason in SPAM_REASONS and await _seams.spam_verdict(account_id) != "clean":
            await _moves.halt_account(ctx, account_id, "spam_block")
            return False
        if item.rest_reason in LIMIT_REASONS and (ends := await limit_ends(ctx, account_id)):
            await repository.rest_account(ctx.campaign_id, account_id, ends[0], ends[1])
            continue
        await repository.wake_account(ctx.campaign_id, account_id)
        await log_event(
            "INFO",
            "chat_broadcast_account_back",
            account_id=account_id,
            extra={"campaign_id": ctx.campaign_id, "reason": item.rest_reason},
        )
        return True


async def _roster_item(ctx: RunContext, account_id: str) -> RosterAccount | None:
    for item in await repository.list_roster(ctx.campaign_id):
        if item.account_id == account_id:
            return item
    return None
