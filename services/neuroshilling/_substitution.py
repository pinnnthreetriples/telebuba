"""Putting a reserve account into the role of one Telegram has finished off.

**Only a confirmed ACCOUNT ban buys a reserve.** ``_telegram.classify_send`` already
separates the two kinds of refusal, and the separation is the whole economics of this
module: ``chat_blocked`` and ``chat_unavailable`` are properties of the CHAT, so a
substitute walks into the identical wall and the campaign has spent an account to learn
nothing; ``chat_wait`` is a delay. ``_dispatch`` therefore hands over only
``account_banned`` and ``account_dead``, through ``RunContext.banned``.

**The verdict is not re-probed.** ``services.neurocomment.bans`` does confirm its ban
with a read-only ``CheckBannedInChannel``, and rightly, but it is answering a different
question: there the probe decides whether to park one (account, channel) PAIR forever,
and ``UserBannedInChannelError`` alone does not prove a per-group ban. Here the question
is about the ACCOUNT, the gateway has already classified it, and the pair-scoped probe
could neither confirm nor deny it — it would only spend one more RPC from a session
Telegram has just refused.

**A substitute has to JOIN before it can say anything.** It has never been in the chat,
so the chat is not in its session entity cache and a send would fail ``not_member``.
:func:`substitute` therefore runs the stand-in through ``_telegram.join_target`` — the
same pacing, the same shared daily join cap and the same presence state machine every
other join uses — and gives up on the target when that does not land, rather than
sending anyway. The join buys the PEER, and nothing else: the reply anchor is a message
id, which no entity cache holds and a freshly joined member may not be shown at all, so
``_steps._replay`` sends the stand-in's first line unattached.

**And then it waits.** ``_telegram.settle_pause`` is awaited after the resolve for the
same reason ``engine._act`` awaits it: joining a group and broadcasting into it in the
same second is the most reportable thing this engine does, and the pacer does not cover
it — a brand-new reserve account has no send history for ``services.pacing`` to space
this call against.

**One ban, one reserve.** The roster swap is a single repository transaction, and what
stops two callers for one ban both reaching the pool is the conditional
``replaced_by_account_id IS NULL`` update: the ban's one substitution slot is either
free or it is not. Which of them lost is reported rather than folded away:
``ReserveSwap.claimed`` separates "the pool is empty" from "somebody else already spent
this ban", and the operator is told the one that happened. On this side of it,
``_steps`` empties ``RunContext.banned`` of the account before calling in, so the run
itself never asks twice.

**The ban is not written here.** ``_dispatch`` records it where the verdict is
classified, because everything in this module is conditional — on the operator's
reserve switch, on the chat still being worth playing — and the account being finished
is not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.logging import log_event
from core.repositories import neuroshilling as repository
from services.neuroshilling import _seams, _telegram

if TYPE_CHECKING:
    from services.neuroshilling._context import RunContext


async def substitute(
    context: RunContext,
    target: str,
    chats: dict[str, int],
    account_id: str,
) -> str | None:
    """Replace ``account_id`` from the reserve pool and get the stand-in into ``target``.

    Returns the stand-in, or ``None`` when the role stays a voice short here — no
    reserve was promoted, or the promoted account could not get into this chat. Each of
    those three outcomes logs a code of its own, so the operator reads which one it was
    instead of inferring it from a missing line.
    """
    campaign_id = context.campaign.campaign_id
    swap = await repository.substitute_banned_account(campaign_id, account_id)
    if swap.stand_in is None:
        await log_event(
            "WARNING",
            # Two different things happened, and the pool count is exactly what the
            # operator is looking at while reading this line.
            "neuroshilling_no_reserve" if swap.claimed else "neuroshilling_ban_already_claimed",
            account_id=account_id,
            extra={"campaign_id": campaign_id, "target": target},
        )
        return None
    stand_in = swap.stand_in
    _swap_roster(context, account_id, stand_in)
    await log_event(
        "INFO",
        "neuroshilling_account_substituted",
        account_id=stand_in,
        extra={"campaign_id": campaign_id, "target": target, "replaces": account_id},
    )
    if await _enter(context, target, chats, stand_in):
        return stand_in
    # The roster swap stands — the stand-in holds the role for every remaining target,
    # which is why it still counts as a substitution — but it says nothing HERE, and
    # nothing else in the log would have said so.
    await log_event(
        "WARNING",
        "neuroshilling_substitute_locked_out",
        account_id=stand_in,
        extra={"campaign_id": campaign_id, "target": target, "replaces": account_id},
    )
    return None


def _swap_roster(context: RunContext, account_id: str, stand_in: str) -> None:
    """Put the stand-in where the banned account stood, in the run's own cast list.

    In place and at the same index, so the joins and the least-busy tie-breaks keep the
    order the roster was read in. The repository row is already written; this is the
    copy every remaining step of every remaining target is dealt from.
    """
    for players in context.by_role.values():
        if account_id in players:
            players[players.index(account_id)] = stand_in


async def _enter(
    context: RunContext,
    target: str,
    chats: dict[str, int],
    account_id: str,
) -> bool:
    """Join and resolve ``target`` for the stand-in, settle, and add it to the chat map.

    Both halves log their own refusal, so a failure here is already explained; what
    this returns is only whether the target can go on.

    The settle pause is the last thing awaited and only on the path that succeeded:
    the stand-in has just joined and is about to publish, which is precisely the
    entry ``engine._act`` pauses after, and this path had been skipping it.
    """
    campaign_id = context.campaign.campaign_id
    state = await _telegram.join_target(campaign_id, account_id, target)
    if state != "joined":
        if state in _telegram.ACCOUNT_HALTED:
            context.halted.add(account_id)
        return False
    resolved = await _telegram.resolve_target(campaign_id, account_id, target)
    if resolved is None:
        return False
    chats[account_id] = resolved.chat_id
    await _seams.sleep(_telegram.settle_pause())
    return True
