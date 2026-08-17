"""Reading a target chat: a poll, a cursor, and one reader account per target.

**A poll and not a subscription, on purpose.** The push listener
(``core.telegram_client._listener``) belongs to neurocomment and carries a
subscription registry a second feature must not disturb; a campaign that
subscribed and unsubscribed around it would change which accounts that feature
believes it is watching with. A cursor read has no shared state at all: the
highest ``message_id`` this campaign has stored for the pair IS the cursor, and
the unique index on the chat log is what makes an overlapping re-poll idempotent.

**One account reads a target.** Not one per speaker: N accounts polling one chat
every half minute is N times the rate limit for exactly the same answer, and the
reads go through ``_seams.execute_read`` — the account lifecycle lock and the run
generation fence — like every other call this domain makes.

**Ownership is decided here, not by the gateway.** Telethon's ``out`` flag answers
"did the READING account write this", so a line said by any other account of ours
arrives looking like a stranger's. Left at that, the fleet would
quote its own scripted dialogue back into its own prompt and offer to answer it —
and an injection that once induced reproduction would keep re-entering the context
from our own side.

Three answers close that, because no one of them covers the whole question. The
send journal holds every SCENARIO step's message id, and nothing else: an autoreply
answers no step, so it has no journal row and the ids alone would have let one of
our accounts read another's autoreply as a stranger's and answer it — a chain of
about ``1/(1-p)`` paid drafts per seed, all of them ours. ``_autoreply`` therefore
writes its own published line straight into this log as ours, which the unique index
turns into a no-op when the next poll reads the same message back. The third answer
is the sender: an ``unconfirmed`` send comes back with no message id at all, so the
only thing left to recognise it by is the account that wrote it.

**Neither id answer is scoped to the campaign**, and that is what stops two fleets
answering each other. Both are facts about the chat and the accounts in it: the
journal is asked for every id OUR fleet put in this target, and the sender set holds
every account the deployment owns. Scoped to the campaign, a second campaign aimed at
the same group saw the first one's autoreplies as a stranger's, took the reply claim
(which reaches across campaigns, so nothing else refused it), answered — and the first
campaign read that answer exactly the same way, each fleet's line entering the other's
prompt labelled ``them``.

The window is the campaign's ``listen_minutes``, measured from the end of the
dialogue in that target. It is per target and the pass is sequential, so a long
window on a long target list is a long campaign; that is the operator's dial and
the launch card states the arithmetic.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from core.repositories import neuroshilling as repository
from core.telegram_client import TelegramReadError
from schemas.neuroshilling import NeuroshillingChatMessage
from schemas.telegram_action_results import ReadChatMessagesResult
from schemas.telegram_actions import ReadChatMessages
from services import pacing
from services.neuroshilling import _autoreply, _seams

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingCampaign
    from schemas.telegram_action_results import ChatMessagePreview
    from services.neuroshilling._context import RunContext

# Stdlib sink for full third-party text — see ``core.proxy_check._failed_result``.
logger = logging.getLogger(__name__)

_SECONDS_PER_MINUTE = 60


def enabled(campaign: NeuroshillingCampaign) -> bool:
    """Does this campaign want its target chats read at all?

    Three switches, any of them: reading the chat, the autoresponder engine, and
    answering real people.

    What the first one buys ON ITS OWN is the observation and nothing else — the
    chat log and the counters on the launch card, which are how an operator sees a
    target is alive before turning anything else on. The only thing that reads the
    log back is ``_autoreply._draft``, and that needs the other two switches; a
    ``revive`` cycle replays its own dialogue and reads nothing.
    """
    return campaign.use_chat_context or campaign.reply_to_humans or campaign.autoresponder != "off"


def _poll_gap() -> float:
    limits = settings.neuroshilling
    return pacing.human_delay(
        limits.poll_min_seconds,
        limits.poll_max_seconds,
        rng=_seams.rng,
        mu=limits.delay_lognorm_mu,
        sigma=limits.delay_lognorm_sigma,
    )


def _reader(context: RunContext, chats: dict[str, int]) -> tuple[str, int] | None:
    """The one account that reads this target, and its own id for the chat.

    First in the map, which is join order, which is the order the cast speaks in —
    stable across polls and across a restart, so the same session does the reading
    all the way through instead of the chat seeing a different member scroll it
    every thirty seconds.
    """
    return next(
        (
            (account_id, chat_id)
            for account_id, chat_id in chats.items()
            if account_id not in context.halted
        ),
        None,
    )


async def _read_page(account_id: str, chat_id: int, cursor: int) -> list[ChatMessagePreview]:
    """One page of the chat above ``cursor``, as the gateway reports it."""
    action = ReadChatMessages(
        chat=str(chat_id),
        min_id=cursor,
        limit=settings.neuroshilling.chat_context_messages,
    )
    result = await _seams.execute_read(account_id, action)
    if not isinstance(result, ReadChatMessagesResult):  # pragma: no cover - union is exhaustive
        message = f"read_chat_messages answered {type(result).__name__}"
        raise TypeError(message)
    return result.messages


async def poll_once(
    context: RunContext,
    target: str,
    chats: dict[str, int],
    deadline: float | None = None,
) -> int:
    """Read what is new in one target and react to it. Returns how many rows are new.

    ``record_chat_messages`` hands back only the rows it actually inserted, which is
    what makes the reply decision fire once per message however far two polls
    overlap.

    The first poll of a target answers nothing. It has no cursor to read above, so what
    comes back is the chat as it stood before we arrived, and every line of it looks
    new; recording it is what gives the next poll a floor.

    ``deadline`` is the listening window's end, and it is checked BETWEEN the answers
    as well as before the poll: a page of twenty messages is twenty model calls and
    twenty sends, so a poll entered a second before the window closed could otherwise
    go on publishing ten minutes after it. The rows are already stored by then, so
    what is dropped is the answering and not the observation.

    A read failure is a WARNING and nothing else: a flood, a dropped socket or a
    lost membership all mean "not this time", and the next poll asks again from the
    same cursor. Nothing is written down, because nothing was learnt.

    The rows land BEFORE the answers are considered, so an exception escaping one
    reply abandons the rest of the page for good rather than re-offering it on the
    next poll. That is the direction to fail in: a message nobody answered is a
    message, and a message answered twice is a bot.
    """
    reader = _reader(context, chats)
    if reader is None:
        return 0
    account_id, chat_id = reader
    campaign_id = context.campaign.campaign_id
    cursor = await repository.chat_cursor(campaign_id, target)
    try:
        page = await _read_page(account_id, chat_id, cursor)
    except TelegramReadError as exc:
        await log_event(
            "WARNING",
            "neuroshilling_chat_poll_failed",
            account_id=account_id,
            extra={"target": target, "kind": exc.kind},
        )
        return 0
    ours = await repository.list_sent_message_ids(target)
    observed = [
        NeuroshillingChatMessage(
            message_id=preview.message_id,
            sender_id=preview.sender_id,
            text=preview.text,
            # Every third of "ours": what this reader sent, what any of our accounts
            # journalled as a scenario step in this chat, and — for the autoreply that
            # never got a message id back — who wrote it. Only the first is on the wire,
            # and only the first is about the campaign doing the reading.
            is_ours=(
                preview.outgoing
                or preview.message_id in ours
                or preview.sender_id in context.our_user_ids
            ),
        )
        for preview in page
    ]
    fresh = await repository.record_chat_messages(campaign_id, target, observed)
    if fresh:
        await log_event(
            "INFO",
            "neuroshilling_chat_polled",
            account_id=account_id,
            extra={"target": target, "seen": len(fresh)},
        )
    if not cursor:
        # The BASELINE poll. ``min_id=0`` asks for the newest page whatever its age, and
        # in a quiet target — which is the normal one — that page is the chat's backlog:
        # every line of it is new to us, so answering the page would answer messages
        # from weeks ago and buy a page of drafts in one poll. The rows are kept, which
        # is what makes the next poll's cursor mean "since we arrived".
        return len(fresh)
    for message in fresh:
        if deadline is not None and _seams.monotonic() >= deadline:
            break
        await _autoreply.consider(context, target, chats, message)
    return len(fresh)


async def listen(context: RunContext, target: str, chats: dict[str, int]) -> None:
    """Poll one target until the campaign's listening window runs out.

    Cancellation is the ordinary exit as well as the exceptional one: Stop cancels
    the run task, and the sleep below is where that lands nine times in ten. It is
    left to propagate — the runtime is what settles a stopped run, and swallowing it
    here would keep polling a campaign the operator has stopped.

    The gap is drawn per poll rather than fixed, for the same reason every other
    pause in this engine is: a request arriving on exactly the same beat is a
    signature, and this one repeats for as long as the window lasts.

    The deadline is handed DOWN as well as tested here. Testing it only at the top of
    the loop bounds when the last poll starts and nothing about how long it runs, and
    a page of twenty messages is twenty answers — so ``listen_minutes`` was a floor
    rather than a window.
    """
    if not enabled(context.campaign) or not chats:
        return
    deadline = _seams.monotonic() + context.campaign.listen_minutes * _SECONDS_PER_MINUTE
    while _seams.monotonic() < deadline:
        await _seams.sleep(_poll_gap())
        try:
            await poll_once(context, target, chats, deadline)
        except (_seams.NeuroshillingRunRevokedError, asyncio.CancelledError):
            raise
        except Exception:
            # One bad poll must not end the window: the dialogue is already in the
            # chat and the account is still in it, so the next poll is a better
            # answer than abandoning the target. The loop cannot spin on a
            # persistent fault, because the sleep above runs before every attempt.
            # Full text to the stdlib sink, never to ``extra``.
            logger.exception("neuroshilling chat poll failed for %s", target)
