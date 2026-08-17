"""The observed-chat log: what the poller saw, and which of it we answered.

**Only the rows this poll actually created are handed back.** Polls overlap by
construction — the cursor is the highest id we have stored, and Telegram is free
to hand the same message to two reads — so ``record_chat_messages`` returns the
INSERTED subset rather than the requested one. Everything downstream of it (the
reply decision, the counters) is therefore once-per-message without any caller
having to remember which ids it has already reacted to.

**Answering is claimed, not observed.** ``claim_chat_reply`` flips ``replied``
under an ``= 0`` predicate, so the decision to answer a message can be made
exactly once whatever becomes of that answer — a refused, failed or filtered
reply must not leave the message open for another attempt, because the retry
would spend a second model call on the same attacker-supplied text.
``record_chat_reply`` is the separate outcome write, and it alone is what the
reply quota counts.

**The reads here use three different keys, one per question they answer.** The poll's
own two — :func:`chat_cursor` and :func:`list_recent_chat` — are scoped by
``(campaign_id, target)``: what THIS campaign has seen in one chat. The reply claim is
keyed on ``(target, message_id)`` across campaigns, because the rows are per campaign
but the chat is not, and two campaigns aimed at one target would otherwise both answer
the same stranger. :func:`count_chat_reply_usage` is keyed on the ACCOUNT — plus the
target for its chat-day half — and names no campaign at all, exactly as the journal's
``read_quota_usage`` counts the same two windows, because those ceilings belong to the
session and the caller adds the two answers together. :func:`count_chat_activity` is
keyed on the campaign alone, across its targets: it is the launch card's own counter
and the card is per campaign.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.db import _get_engine, _now_iso
from core.repositories.neuroshilling._tables import _neuroshilling_chat_log
from schemas.neuroshilling import (
    NeuroshillingChatActivity,
    NeuroshillingChatMessage,
    NeuroshillingQuotaUsage,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_TABLE = _neuroshilling_chat_log


def _at(campaign_id: str, target: str) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    """The two predicates every query here starts from: one chat of one campaign."""
    return (_TABLE.c.campaign_id == campaign_id, _TABLE.c.target == target)


def _chat_cursor(campaign_id: str, target: str) -> int:
    statement = select(func.max(_TABLE.c.message_id)).where(*_at(campaign_id, target))
    with _get_engine().connect() as connection:
        return int(connection.execute(statement).scalar() or 0)


async def chat_cursor(campaign_id: str, target: str) -> int:
    """The highest message id stored for this pair, or ``0`` if nothing is.

    ``0`` is a meaningful cursor rather than a missing one: the gateway reads it as
    "the newest page of the chat", which is where a first poll has to start. Walking
    forward from the beginning of a chat's history instead would spend a poll per
    page and answer messages from years ago on the way.
    """
    return await asyncio.to_thread(_chat_cursor, campaign_id, target)


def _record_chat_messages(
    campaign_id: str,
    target: str,
    messages: Sequence[NeuroshillingChatMessage],
) -> list[NeuroshillingChatMessage]:
    fresh: list[NeuroshillingChatMessage] = []
    seen_at = _now_iso()
    with _get_engine().begin() as connection:
        for message in messages:
            statement = (
                sqlite_insert(_TABLE)
                .values(
                    campaign_id=campaign_id,
                    target=target,
                    message_id=message.message_id,
                    sender_id=message.sender_id,
                    text=message.text,
                    is_ours=int(message.is_ours),
                    seen_at=seen_at,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        _TABLE.c.campaign_id,
                        _TABLE.c.target,
                        _TABLE.c.message_id,
                    ],
                )
            )
            if connection.execute(statement).rowcount > 0:
                fresh.append(message)
    return fresh


async def record_chat_messages(
    campaign_id: str,
    target: str,
    messages: Sequence[NeuroshillingChatMessage],
) -> list[NeuroshillingChatMessage]:
    """Store what a poll saw; return only the messages this call actually inserted.

    One transaction and one statement per row, because the answer the caller needs
    is per-row: a bulk insert would say how many landed but not which, and "which"
    is what makes the reply decision fire once per message.
    """
    if not messages:
        return []
    return await asyncio.to_thread(_record_chat_messages, campaign_id, target, list(messages))


def _list_recent_chat(campaign_id: str, target: str, limit: int) -> list[NeuroshillingChatMessage]:
    statement = (
        select(_TABLE.c.message_id, _TABLE.c.sender_id, _TABLE.c.text, _TABLE.c.is_ours)
        .where(*_at(campaign_id, target))
        .order_by(_TABLE.c.message_id.desc())
        .limit(limit)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).all()
    return [
        NeuroshillingChatMessage(
            message_id=int(row[0]),
            sender_id=None if row[1] is None else int(row[1]),
            text=str(row[2] or ""),
            is_ours=bool(row[3]),
        )
        for row in reversed(rows)
    ]


async def list_recent_chat(
    campaign_id: str,
    target: str,
    *,
    limit: int,
) -> list[NeuroshillingChatMessage]:
    """The newest ``limit`` observed messages, handed back OLDEST first.

    Selected newest-first and reversed, because "the newest N" is the question and
    "in the order they were said" is the shape a conversation has to be read in.
    """
    return await asyncio.to_thread(_list_recent_chat, campaign_id, target, limit)


def _claim_chat_reply(campaign_id: str, target: str, message_id: int) -> bool:
    elsewhere = _TABLE.alias("claimed_elsewhere")
    statement = (
        update(_TABLE)
        .where(
            *_at(campaign_id, target),
            _TABLE.c.message_id == message_id,
            _TABLE.c.replied == 0,
            ~select(elsewhere.c.id)
            .where(
                elsewhere.c.target == target,
                elsewhere.c.message_id == message_id,
                elsewhere.c.replied == 1,
            )
            .exists(),
        )
        .values(replied=1)
    )
    with _get_engine().begin() as connection:
        return connection.execute(statement).rowcount > 0


async def claim_chat_reply(campaign_id: str, target: str, message_id: int) -> bool:
    """Take the right to answer one message. ``False`` means somebody already has it.

    Claimed BEFORE the model is asked and never released, which is the point: an
    answer that is refused by the output gate, or that fails to send, must not leave
    the message open for a second attempt. Retrying would pay for another model call
    on the same attacker-supplied text and could publish on the second roll of the
    dice what the first one caught.

    "Somebody" reaches across campaigns, which is why the ``NOT EXISTS`` is there.
    The rows are per campaign, but the CHAT is not: two campaigns pointed at the same
    target both observe the same message, and scoping the claim to the campaign let
    both answer it — two of our accounts replying to one stranger within a minute,
    from two different fleets, which is the bot tell this whole engine is arranged
    around avoiding. One statement rather than a read then a write, so the check and
    the claim cannot be straddled.
    """
    return await asyncio.to_thread(_claim_chat_reply, campaign_id, target, message_id)


def _record_chat_reply(campaign_id: str, target: str, message_id: int, account_id: str) -> None:
    statement = (
        update(_TABLE)
        .where(*_at(campaign_id, target), _TABLE.c.message_id == message_id)
        .values(reply_account_id=account_id, replied_at=_now_iso())
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def record_chat_reply(
    campaign_id: str,
    target: str,
    message_id: int,
    *,
    account_id: str,
) -> None:
    """Record that an answer was PUBLISHED, and by whom.

    Separate from the claim above because the two mean different things: the claim
    is "this message has been decided about", this is "an account spent a send on
    it". Only the second may count against a quota.
    """
    await asyncio.to_thread(_record_chat_reply, campaign_id, target, message_id, account_id)


def _count_chat_reply_usage(
    account_id: str,
    target: str,
    hour_since: str,
    day_since: str,
) -> NeuroshillingQuotaUsage:
    by_account = _TABLE.c.reply_account_id == account_id
    hour = select(func.count()).where(by_account, _TABLE.c.replied_at >= hour_since)
    chat_day = select(func.count()).where(
        by_account,
        _TABLE.c.target == target,
        _TABLE.c.replied_at >= day_since,
    )
    with _get_engine().connect() as connection:
        return NeuroshillingQuotaUsage(
            hour=connection.execute(hour).scalar_one(),
            chat_day=connection.execute(chat_day).scalar_one(),
        )


async def count_chat_reply_usage(
    account_id: str,
    target: str,
    *,
    hour_since: str,
    day_since: str,
) -> NeuroshillingQuotaUsage:
    """Published autoreplies by this account, against the hour and chat-day ceilings.

    An autoreply is not a scenario step, so it has no ``neuroshilling_messages`` row
    and the journal's quota read cannot see it. Counted here and ADDED to that read
    by the caller, because the ceilings in the form belong to the account and it is
    the same account publishing both kinds of message.

    Neither window is narrowed to a campaign, and neither is the journal half the
    caller adds: an account two campaigns share carries both campaigns' sends into both
    counts, which is what makes the sum mean "what this session has said" — the thing
    Telegram is rate-limiting. Scoping one half and not the other would make the sum a
    number neither ceiling describes.

    ``campaign_total`` is left at zero: the lifetime ceiling is worded per campaign
    and this table is not keyed by account and campaign together, so answering it
    here would need a second index for a number the journal already dominates.
    """
    return await asyncio.to_thread(
        _count_chat_reply_usage,
        account_id,
        target,
        hour_since,
        day_since,
    )


def _count_chat_activity(campaign_id: str) -> NeuroshillingChatActivity:
    by_campaign = _TABLE.c.campaign_id == campaign_id
    seen = select(func.count()).where(by_campaign)
    replied = select(func.count()).where(by_campaign, _TABLE.c.replied_at.is_not(None))
    with _get_engine().connect() as connection:
        return NeuroshillingChatActivity(
            seen=connection.execute(seen).scalar_one(),
            replied=connection.execute(replied).scalar_one(),
        )


async def count_chat_activity(campaign_id: str) -> NeuroshillingChatActivity:
    """The two listener counters the launch card shows, in one thread hop.

    ``replied`` counts rows with a ``replied_at``, not rows with ``replied`` set: the
    flag is the decision and the timestamp is the publication, and the operator is
    asking how many answers really went out.
    """
    return await asyncio.to_thread(_count_chat_activity, campaign_id)
