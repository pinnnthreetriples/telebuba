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

While it rests its chats are not abandoned: a rest of a minute keeps them; past that each
chat goes to an account that would write there before the owner is back — one already
inside it first (it writes at once), a stranger only when its join and the pause after it
still beat the rest — and to no account beyond the sends it has left. The run with
every account resting waits for the first one back instead of stalling, and the scheduler
re-applies those thresholds to the rest that is left (``place_resting``), so a chat that
comes up for its next round while its owner still rests is not kept waiting for it.
"""

from __future__ import annotations

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
    from collections.abc import Awaitable, Callable

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
# The rest a spam block takes, whether Telegram just refused a send (PeerFlood) or the block was
# already known and the account is rested before it can be discovered by sending.
BLOCK_REST_REASON: Final = "peer_flood"
SPAM_REASONS: Final = frozenset({BLOCK_REST_REASON, _BANNED_IN_CHAT})
LIMIT_REASONS: Final = frozenset({"hour_limit", "day_limit"})
# Halts a new Start or Continue does not undo: the account itself is gone or full. Any
# other halt (errors in a row, a spam block still standing, a pre-rotation flood) gets
# another chance — the operator pressing Start is the decision to try it again.
FINAL_HALT_REASONS: Final = frozenset(
    {"account_deactivated", "account_frozen", "session_dead", "ChannelsTooMuchError"}
)

Resting = Literal["resting"]


def held(item: RosterAccount, campaign_id: str) -> bool:
    return (
        item.state == "active"
        and _account_owner.owner_of(item.account_id) == _OWNER
        and _account_owner.holder_of(item.account_id) == campaign_id
    )


async def live_accounts(campaign_id: str) -> list[str]:
    """Roster accounts in the run, held by it and not resting, in roster order."""
    return (await accounts_by_rest(campaign_id))[0]


async def resting_accounts(campaign_id: str) -> list[str]:
    """Roster accounts held by the run that are sitting a rest out."""
    return (await accounts_by_rest(campaign_id))[1]


async def accounts_by_rest(campaign_id: str) -> tuple[list[str], list[str]]:
    """The live and the resting accounts from ONE read of the roster.

    Two reads can straddle a wake-up and see the account in neither list, which looks like
    an account that left: its chats would be taken from it.
    """
    live: list[str] = []
    resting: list[str] = []
    for item in await repository.list_roster(campaign_id):
        if held(item, campaign_id):
            (live if item.resting_until_unix is None else resting).append(item.account_id)
    return live, resting


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


async def spare_sends(ctx: RunContext, account_id: str) -> int | None:
    """Sends the account's hourly and daily limits still allow now; ``None`` = no limit."""
    if not ctx.settings.account_limit:
        return None
    now = _seams.now()
    left = [
        cap - len(await repository.list_account_send_times_since(account_id, now - window))
        for window, cap in ((_HOUR, ctx.settings.per_hour), (_DAY, ctx.settings.per_day))
    ]
    return max(0, min(left))


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


async def place_resting(ctx: RunContext) -> int:
    """Place the chats of every resting account by the rest it has LEFT; chats moved.

    A chat that came up for its next round during a long rest would otherwise wait for
    its owner. Idempotent: a chat already given to a live account is not touched again.
    """
    now = _seams.now()
    handed = 0
    for item in await repository.list_roster(ctx.campaign_id):
        if not held(item, ctx.campaign_id) or item.resting_until_unix is None:
            continue
        if (remaining := item.resting_until_unix - now) > 0:
            handed += await _place_chats(ctx, item.account_id, remaining)
    return handed


async def _place_chats(ctx: RunContext, account_id: str, seconds: int) -> int:
    """Lend the resting account's chats to whoever writes there before it is back.

    Each account takes no more chats than it has sends left. Asked again on every tick
    (``place_resting``), so a chat kept now moves as soon as an account inside it has room.
    """
    if seconds <= settings.chat_broadcast.keep_chats_rest_seconds:
        return 0
    spare: dict[str, int | None] = {}
    for other in await live_accounts(ctx.campaign_id):
        if other != account_id and (left := await spare_sends(ctx, other)) != 0:
            spare[other] = left
    if not spare:
        # Nobody can take them now: they stay with it and it picks them up when back.
        return 0
    return await _moves.hand_over(
        ctx, account_id, list(spare), back_at=_seams.now() + seconds, spare=spare
    )


async def rest_known_block(
    ctx: RunContext, account_id: str, target: TargetRecord
) -> Resting | None:
    """A spam block the account is already KNOWN to carry rests it, as a PeerFlood would.

    The stored status only (``_seams.known_spam_status``, no Telegram), asked before the
    account joins or writes anything, so the block is not discovered by sending. The rest is the
    one a PeerFlood takes (``spam_rest_seconds``, the same reason): at its end @SpamBot is asked
    (``sit_out``): only a block still standing takes the account out, while a postponed, refused
    or failed answer keeps it resting. An unknown status does not rest the account.
    """
    if await _seams.known_spam_status(account_id) != "limited":
        return None
    return await rest_for(
        ctx, account_id, settings.chat_broadcast.spam_rest_seconds, BLOCK_REST_REASON, target
    )


async def _spam_verdict(account_id: str, caller: str) -> _seams.SpamVerdict:
    """@SpamBot's answer for this account, named by who is asking."""
    with _seams.spam_caller(caller):
        return await _seams.spam_verdict(account_id)


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
    ban: only the chat moves (once; a chat that already moved is skipped, the account
    goes on). A dead session leaves.
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
        and await _spam_verdict(account_id, "chat_broadcast.banned") == "clean"
    ):
        if target.handed_from_account_id is None:
            await reroute(ctx, account_id, target, "banned")
        else:
            # Moved once already and banned again: the chat does not want us. The account is
            # clean — it must not sit out hours for a ban it does not carry.
            await _moves.skip(ctx, target, "banned", account_id)
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
    refuses us, and with nobody to move it to it is skipped for ``reason``. Nobody means
    nobody it has not banned or muted already: it is never given back to one of those.
    """
    fresh = await repository.fetch_target(ctx.campaign_id, target.chat_key) or target
    await repository.add_refusal(ctx.campaign_id, target.chat_key, account_id)
    refused = fresh.model_copy(update={"refused_by": [*fresh.refused_by, account_id]})
    others = _moves.receivers(await room(ctx, account_id), refused) or _moves.receivers(
        [a for a in await live_accounts(ctx.campaign_id) if a != account_id], refused
    )
    load = _moves.open_load(await repository.list_targets(ctx.campaign_id))
    to = _moves.least_loaded(others, load)
    if to is None:
        await _moves.skip(ctx, fresh, reason, account_id)
        return
    if await _moves.assign(ctx, fresh, to):
        await log_event(
            "WARNING",
            "chat_broadcast_chat_rerouted"
            if reason == "banned"
            else "chat_broadcast_muted_rerouted",
            account_id=account_id,
            extra={"campaign_id": ctx.campaign_id, "chat": _moves.chat_ref(target), "to": to},
        )


async def limit(ctx: RunContext, account_id: str, target: TargetRecord) -> Resting | None:
    """Rest until its limit lets it write again; ``None`` when it has room after all."""
    ends = await limit_ends(ctx, account_id)
    if ends is None:
        return None
    until, reason = ends
    return await rest_until(ctx, account_id, until, reason, target)


async def sit_out(
    ctx: RunContext,
    account_id: str,
    meanwhile: Callable[[], Awaitable[object]] | None = None,
) -> bool:
    """Wait the account's rest out. ``True`` = back in the run, ``False`` = it left.

    Slept in slices, re-reading the end each time. At the
    end a spam block is checked with @SpamBot and a limit is counted again — a send by
    another campaign may have used the room up meanwhile.

    ``meanwhile`` runs before each slice of a rest on the account's OWN limit — Telegram has
    not stopped it, only the operator's count has — so it can get ready for its return. A rest
    Telegram imposed (a flood, a spam block) runs nothing.
    """
    tick = settings.chat_broadcast.rest_poll_seconds
    while True:
        item = await _roster_item(ctx, account_id)
        if item is None or item.state != "active":
            return False
        if item.resting_until_unix is None:
            return True
        if (remaining := item.resting_until_unix - _seams.now()) > 0:
            if meanwhile is not None and item.rest_reason in LIMIT_REASONS:
                await meanwhile()
                remaining = item.resting_until_unix - _seams.now()
            await _seams.sleep(max(1, min(remaining, tick)))
            continue
        _seams.assert_live_run()
        if item.rest_reason in SPAM_REASONS:
            verdict = await _spam_verdict(account_id, "chat_broadcast.rest_end")
            if verdict == "limited":
                await _moves.halt_account(ctx, account_id, "spam_block")
                return False
            if verdict != "clean":
                # No reading (the budget said later, the guard refused, the probe failed): that
                # is not a block, so the account keeps resting and is asked again on the next
                # tick instead of being halted (D9).
                await _seams.sleep(tick)
                continue
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
