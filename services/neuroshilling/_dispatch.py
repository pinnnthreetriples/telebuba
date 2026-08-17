"""Publishing one already-claimed step: the content gate, the send, and the verdict.

Split from ``_steps`` for the file-size budget. That module decides WHO speaks and
reserves the journal row; this one decides whether the words may go out at all, aims
them, sends them, and turns the answer into a settled row plus a log line.

**Every send is vetted first.** ``services.content`` exists because identical wording
published from several accounts is a strong spam signal, and every other publishing
path in the project runs these two calls. This one differs in a single respect: the
dedup reservation is taken on the text SCOPED TO ITS TARGET. The other callers generate
a fresh line per send, whereas a campaign replays ONE approved dialogue into many chats
on purpose — global dedup would let the first target through and refuse every other as
a duplicate of it. Scoped, the gate still fires on what really is a signal: the same
words twice in the same chat, including a re-run of a dialogue that chat already has.

**One outcome is never settled.** A dispatch already on the wire when the connection
died answers ``unconfirmed``; Telegram may have applied it, so the row stays ``pending``
and nothing ever retries it. The boot sweep turns those into ``failed`` without deleting
them, so the unique key stays occupied either way.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Final

from core.channel_tokens import parse_message_link
from core.logging import log_event
from core.repositories import neuroshilling as repository
from schemas.neuroshilling import NeuroshillingStepKey
from schemas.telegram_actions import CopyMessageMedia, PostComment
from services.content import is_acceptable, release_sent_text, try_reserve_sent
from services.neuroshilling import _seams, _telegram

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingCampaign
    from schemas.neuroshilling_scenario import NeuroshillingStep
    from schemas.telegram_actions import ActionResult, TelegramAction
    from services.neuroshilling._context import RunContext

# Stdlib sink for full third-party text — see ``core.proxy_check._failed_result``.
logger = logging.getLogger(__name__)

# Verdicts that finish the ACCOUNT rather than delay it: a session that cannot act at
# all, and the one ban Telegram reports as itself. Only these two are worth a reserve
# account — a flood is a wait, and the chat-scoped refusals below meet a substitute
# identically. Which of the two it was is carried on into ``RunContext.banned``,
# because only ``account_banned`` says anything about the chat it happened in.
_BANS_ACCOUNT: Final = frozenset({"account_dead", "account_banned"})
# Verdicts that take the ACCOUNT out of the run: Telegram is rate-limiting it, or it
# cannot act at all. ``_telegram.record_send_verdict`` has already persisted the first
# kind onto every presence row, so the halt outlives this process too. Owned by
# ``_telegram`` because the autoreply path acts on the same set.
_HALTS_ACCOUNT: Final = _telegram.HALTS_ACCOUNT
# Verdicts that belong to the CHAT. Another account of the same role meets them
# identically, so the target is abandoned rather than retried with a second session.
_LOSES_TARGET: Final = frozenset({"chat_blocked", "chat_unavailable"})
# The two outcomes that must NOT give the dedup reservation back: one published the
# text, and one may have.
_KEEPS_RESERVATION: Final = frozenset({"sent", "unconfirmed"})


def dedup_key(target: str, text: str) -> str:
    """The string the dedup reservation is taken on — see the module docstring."""
    return f"{target}\n{text}"


def _key(context: RunContext, target: str, step: NeuroshillingStep) -> NeuroshillingStepKey:
    return NeuroshillingStepKey(run_id=context.run_id, target=target, step_id=step.step_id)


async def vet_text(
    context: RunContext,
    target: str,
    step: NeuroshillingStep,
    account_id: str,
    text: str,
) -> bool:
    """Run the outbound content filter and claim the text is not a repeat here."""
    if not is_acceptable(text):
        await skip(context, target, step, "neuroshilling_text_rejected", account_id)
        return False
    if not await try_reserve_sent(dedup_key(target, text)):
        await skip(context, target, step, "neuroshilling_text_duplicate", account_id)
        return False
    return True


async def skip(
    context: RunContext,
    target: str,
    step: NeuroshillingStep,
    event: str,
    account_id: str,
) -> None:
    """Settle a claimed row that will never be sent, and say why."""
    await repository.settle_message(_key(context, target, step), status="skipped")
    await log_event("WARNING", event, account_id=account_id, extra={"target": target})


async def resolve_reply_to(
    context: RunContext,
    target: str,
    step: NeuroshillingStep,
) -> int | None:
    """The message id this step answers IN THIS TARGET, climbing past gaps.

    Keyed on ``(run_id, target, step at reply_to_position)`` and never on the step
    alone: the link belongs to the campaign, the id belongs to one send into one chat.

    A step whose anchor was skipped or failed has no id, so the walk follows the
    anchor's own ``reply_to_position`` to the nearest earlier step that does have one.

    Every stored link really is strictly backwards, but by two different mechanisms:
    ``scenario._backward_link_problem`` refuses a forward one on the PUT, and a
    generated dialogue gets there another way — ``_generate._to_update`` fills
    ``position_of`` only after a step is kept, so a link to a later step resolves to
    nothing at all. Rather than inherit an invariant from two places, the walk enforces
    its own: every hop must land strictly lower than the last or the loop stops, so a
    link that did point forward — or at itself — costs one round trip instead of
    spinning against the database.

    Reaching the top with nothing found sends the message unattached, and says so: a
    silently broken chain in a staged dialogue reads worse to a human than a gap.
    """
    position, previous = step.reply_to_position, step.position
    while position is not None and position < previous:
        anchor = context.by_position.get(position)
        if anchor is None:
            break
        message_id = await repository.fetch_message_id(_key(context, target, anchor))
        if message_id is not None:
            return message_id
        position, previous = anchor.reply_to_position, position
    if step.reply_to_position is not None:
        await log_event(
            "WARNING",
            "neuroshilling_reply_anchor_lost",
            extra={"target": target, "position": step.position},
        )
    return None


def media_source(
    campaign: NeuroshillingCampaign,
    step: NeuroshillingStep,
) -> tuple[str, int] | None:
    """The chat and message id this step copies media from, if it is the media step."""
    if campaign.media_step_position != step.position or not campaign.media_message_link:
        return None
    return parse_message_link(campaign.media_message_link)


def build_message_action(
    campaign: NeuroshillingCampaign,
    step: NeuroshillingStep,
    chat_id: int,
    text: str,
    reply_to: int | None,
) -> TelegramAction:
    """A copy of the campaign's media if this is the media step, else a plain send.

    A COPY and never a forward: a forward renders "Forwarded from ..." and links back
    to the source, which is the one thing a staged conversation must not do.
    """
    source = media_source(campaign, step)
    if source is None:
        return PostComment(chat_id=chat_id, text=text, reply_to=reply_to)
    return CopyMessageMedia(
        chat_id=chat_id,
        source_chat=source[0],
        source_message_id=source[1],
        caption=text,
        reply_to=reply_to,
    )


async def dispatch(
    context: RunContext,
    target: str,
    step: NeuroshillingStep,
    account_id: str,
    action: TelegramAction,
) -> bool:
    """Send the claimed step and settle its row. ``False`` abandons the target."""
    try:
        result = await _seams.execute(account_id, action)
    except (_seams.NeuroshillingRunRevokedError, asyncio.CancelledError):
        # Stop, or shutdown. The row stays ``pending`` and the boot sweep settles it,
        # exactly like a dispatch whose outcome we never learnt — because that is what
        # this is.
        raise
    except Exception as exc:
        logger.exception("neuroshilling step failed for %s", account_id)
        await _settle_failure(context, target, step, account_id, type(exc).__name__)
        return True
    return await _record(context, target, step, account_id, result)


async def _record(
    context: RunContext,
    target: str,
    step: NeuroshillingStep,
    account_id: str,
    result: ActionResult,
) -> bool:
    """Turn one outcome into a settled row, a log line and a keep-going answer.

    Classified HERE rather than by the caller, because the classification is not a
    pure read: ``record_send_verdict`` also persists an account-wide flood across the
    presence rows, and splitting the two would let them drift.
    """
    campaign_id = context.campaign.campaign_id
    verdict = await _telegram.record_send_verdict(campaign_id, account_id, target, result)
    if verdict not in _KEEPS_RESERVATION and step.kind == "message":
        # Nothing was published, so the dedup reservation this text took must go back:
        # a later run of the same campaign would otherwise refuse it as a repeat of a
        # send that never happened. Empty text included, and that is the point — a
        # media step with no caption reserved the bare target's hash, so one failed
        # media send held it for the whole 7-day window and blocked every other
        # captionless media step into that chat.
        await release_sent_text(dedup_key(target, step.text.strip()))
    if verdict == "sent":
        await repository.settle_message(
            _key(context, target, step),
            status="sent",
            message_id=result.message_id,
        )
        await log_event(
            "INFO",
            "neuroshilling_message_sent",
            account_id=account_id,
            extra={"target": target, "position": step.position},
        )
        return True
    if verdict == "unconfirmed":
        # Left ``pending`` on purpose and never retried: the request was already on the
        # wire, so Telegram may have applied it. The row goes on holding its key.
        await log_event(
            "WARNING",
            "neuroshilling_message_unconfirmed",
            account_id=account_id,
            extra={"target": target, "position": step.position},
        )
        return True
    if verdict == "chat_wait":
        await skip(context, target, step, "neuroshilling_step_skipped_slow_mode", account_id)
        return True
    await _settle_failure(context, target, step, account_id, result.error_type)
    if verdict in _HALTS_ACCOUNT:
        context.halted.add(account_id)
        if verdict in _BANS_ACCOUNT:
            # Persisted HERE, where the verdict is classified, because this is the only
            # point every ban passes through: the reserve switch may be off and the
            # chat may already be abandoned, and the account is finished either way.
            await repository.ban_campaign_account(campaign_id, account_id)
            # And handed to ``_steps``, which is the layer that owns the target's chat
            # map and can therefore get a stand-in into the chat before it speaks. The
            # verdict travels with it: only one of the two is evidence about the CHAT.
            context.banned[account_id] = verdict
        await log_event(
            "WARNING",
            "neuroshilling_account_halted",
            account_id=account_id,
            extra={"target": target, "verdict": verdict},
        )
    if verdict in _LOSES_TARGET:
        await log_event(
            "WARNING",
            "neuroshilling_target_failed",
            account_id=account_id,
            extra={"target": target, "verdict": verdict},
        )
        return False
    return True


async def _settle_failure(
    context: RunContext,
    target: str,
    step: NeuroshillingStep,
    account_id: str,
    error_type: str | None,
) -> None:
    await repository.settle_message(
        _key(context, target, step),
        status="failed",
        error_type=error_type,
    )
    await log_event(
        "WARNING",
        "neuroshilling_message_failed",
        account_id=account_id,
        extra={"target": target, "position": step.position}
        | ({"error_type": error_type} if error_type else {}),
    )
