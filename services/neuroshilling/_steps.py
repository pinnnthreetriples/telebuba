"""One step of one target: who speaks it, and the reservation that lets them.

**The row goes in first.** :func:`play_step` writes a ``pending`` journal row through
``claim_message`` BEFORE anything is dispatched. Sending first and writing second leaves
a window in which Telegram has the message and SQLite has nothing, so the next boot
replays the step into a chat that already has it — the unique index cannot help, because
it only protects rows that exist. The claim's ``False`` return is also what makes a
resumed run walk past work it already did.

**The quota re-count and the insert share one lock.** Autoreplies take that lock
while claiming an in-flight quota slot, then release it before model and Telegram
I/O. Roles belong to the campaign, so
the same account plays the same role in every target, and two campaigns may share an
account outright: without the lock both read an under-cap count and both publish.

That lock is this module's own private map, and it is RELEASED before anything is
dispatched — :func:`_reserve` closes its ``async with`` and the send happens after it
returns. So the quota lock and ``services.warming.account_lock`` (which
``_seams.execute`` takes) are never held at the same time, in either order. The
alternative, holding the quota lock across the send, would nest it outside a LIFECYCLE
mutex that ``services.accounts.lifecycle`` keeps across several awaits — which is a
deadlock waiting for an operator to press remove.

Publishing the reserved step is ``_dispatch``'s half; this module stops at the row.
Who may speak at all — the voices a chat still listens to, and whether the dialogue
has opened there — is ``_cast``'s.

**The one thing this module publishes itself is a replay.** A line whose speaker could
not say it is said again from another session — over the SAME journal row, handed
across rather than inserted, because ``(run_id, target, step_id)`` is unique and the
failed row already holds it. Two things earn one: a chat that refused the account, which
passes the line to the next account of the role already inside, and a confirmed ACCOUNT
ban, for which ``_substitution`` promotes a reserve account and gets it into the chat.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, NamedTuple, get_args

from core.logging import log_event
from core.repositories import neuroshilling as repository
from schemas.neuroshilling import NeuroshillingStepKey
from schemas.telegram_actions import ReactToMessage
from schemas.telegram_actions_chat import ChatReactionEmoji
from services.neuroshilling import _dispatch, _quota_ledger, _seams, _substitution
from services.neuroshilling._cast import Voices, opening_stands, pick_account, step_delay
from services.neuroshilling._quota_ledger import account_lock, pending_reply_usage

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingCampaign
    from schemas.neuroshilling_scenario import NeuroshillingStep
    from services.neuroshilling._context import RunContext

_REACTIONS: Final[frozenset[ChatReactionEmoji]] = frozenset(get_args(ChatReactionEmoji))


def reset_for_tests() -> None:
    _quota_ledger.reset_for_tests()


class _Speaker(NamedTuple):
    """The account a failed line is handed to, and what it can see of the chat.

    ``sees_history`` is the difference between the two replays. A sibling of the role
    has been in the chat since the pass entered it, so the anchor it answers is a
    message it was shown; a stand-in joined a moment ago and may be shown none.
    """

    account_id: str
    chat_id: int
    sees_history: bool = True


class _Reserved(NamedTuple):
    """The result of the locked [re-count -> insert] section."""

    claimed: bool
    quota_reason: str | None


async def play_step(
    context: RunContext,
    target: str,
    step: NeuroshillingStep,
    voices: Voices,
) -> bool:
    """Play one step into one target. ``False`` means stop playing this target.

    **A line is lost only when nobody of its role can say it.** An account the chat
    refuses is silenced for this target and the SAME journal row is handed to the next
    account of the role, until one publishes it or none is left. A role with nobody
    left costs that one step and no more: the dialogue goes on, because the lines after
    it — the closing one above all — are what the campaign is for, and the reply walk
    in ``_dispatch.resolve_reply_to`` already climbs past a gap.

    **Nothing is played on top of a dialogue that never opened** — see
    ``_cast.opening_stands``. That is the one case that still ends the target.

    The delay comes first and the speaker is picked after it, so selection scores the
    hourly load as it is at the moment of sending rather than as it was a minute ago.
    """
    if not await opening_stands(context, target, step):
        return False
    await _seams.sleep(step_delay(step))
    chats = voices.chats
    handed_on = False
    while True:
        account_id = await pick_account(context, step, voices)
        if account_id is None:
            await log_event(
                "WARNING",
                "neuroshilling_role_exhausted",
                extra={
                    "campaign_id": context.campaign.campaign_id,
                    "target": target,
                    "position": step.position,
                },
            )
            return True
        if step.kind == "reaction":
            played = await _play_reaction(context, target, step, account_id, chats[account_id])
        elif handed_on:
            played = await _replay(context, target, step, _Speaker(account_id, chats[account_id]))
        else:
            played = await _play_message(context, target, step, account_id, chats[account_id])
        # Answered here, by the step that put it there, so nothing is ever left in the
        # map for a later step to act on. The ban is already on the roster row either
        # way — ``_dispatch`` wrote it where the verdict was classified — so everything
        # below is only about who says the rest of the dialogue.
        verdict = context.banned.pop(account_id, None)
        if verdict is not None:
            if not context.campaign.reserve_enabled:
                # The operator's switch. With it off the role simply goes a voice
                # short: the account is already in ``halted``, its siblings play the
                # rest of the dialogue, and no reserve is touched.
                return played
            return await _substitute(
                context, target, step, _substitution.Ban(account_id, verdict), chats
            )
        if played:
            return True
        # ``_dispatch`` answers ``False`` for one thing only: this chat refused THIS
        # account. It has no voice here for the rest of the telling.
        voices.silenced.add(account_id)
        if step.kind == "reaction":
            # Not handed on, for the reason a ban does not replay one: it is counted in
            # no ceiling and no progress, and a second aim buys nothing.
            return True
        handed_on = True


async def _substitute(
    context: RunContext,
    target: str,
    step: NeuroshillingStep,
    ban: _substitution.Ban,
    chats: dict[str, int],
) -> bool:
    """Replace a banned speaker and let the stand-in say the line it lost.

    ``False`` only when the CHAT is the cause — a second ban in it — which abandons this
    target and leaves the rest of the campaign's targets to run. With nobody to take
    over the role is a voice short here and the dialogue goes on without that line,
    exactly as it does with the operator's reserve switch off.

    A reaction is not replayed. It is journalled but counted in neither the progress
    numerator nor the campaign's ceilings, and the stand-in is in the cast from now on
    either way, so the replay would buy only a second aim at the same anchor.

    A stand-in that loses its own replay is taken out of ``context.banned`` here and
    weighed against the same target. The substitution is not chained: a chat that bans
    on sight would otherwise empty the pool one account at a time, and
    ``record_send_verdict`` has already retired the stand-in across its presence rows.
    """
    if await _substitution.chat_bans_the_fleet(context, target, ban):
        return False
    stand_in = await _substitution.substitute(context, target, chats, ban.account_id)
    if stand_in is None or step.kind == "reaction":
        return True
    speaker = _Speaker(stand_in, chats[stand_in], sees_history=False)
    played = await _replay(context, target, step, speaker)
    stand_in_verdict = context.banned.pop(stand_in, None)
    if stand_in_verdict is None:
        return played
    await _substitution.chat_bans_the_fleet(
        context, target, _substitution.Ban(stand_in, stand_in_verdict)
    )
    # The target is lost whatever the replay itself answered: the role has now spent two
    # accounts on this chat, and a third would be the same bet again.
    return False


async def _replay(
    context: RunContext,
    target: str,
    step: NeuroshillingStep,
    speaker: _Speaker,
) -> bool:
    """Send the failed step again from another session, over its own row.

    The quota is re-counted against the NEW speaker under its own lock, exactly as a
    first attempt would be: the ceilings belong to the account, and handing a row over
    moves it from one account's hourly tally to another's.

    The dedup reservation was given back when the failure settled the row, so the vet
    runs from scratch — including its answer for a text this pass has already said here.

    A stand-in's line goes out UNATTACHED, and never through
    ``_dispatch.resolve_reply_to``. The anchor is a message id read from the journal,
    and the stand-in has just joined a chat whose history it may not be shown at all —
    the standard anti-spam setting on the groups this targets — where Telegram answers
    ``MESSAGE_ID_INVALID`` to a reply aimed at it. That is not one of
    ``_telegram._ERROR_VERDICTS``, so it would settle as a generic failure and the line
    would vanish for a reason nothing recorded. Sending it unattached is the same
    degradation ``resolve_reply_to`` already falls back to when an anchor is lost, and
    it logs the same code. A sibling of the role was in the chat all along, so its line
    keeps the anchor.
    """
    account_id, chat_id = speaker.account_id, speaker.chat_id
    async with account_lock(account_id):
        reason = await _quota_reason(context.campaign, account_id, target)
        handed = reason is None and await repository.hand_over_message(
            _key(context, target, step),
            account_id=account_id,
        )
    if reason is not None:
        await log_event(
            "WARNING",
            "neuroshilling_step_skipped_quota",
            account_id=account_id,
            extra={"target": target, "reason": reason},
        )
        return True
    if not handed:
        # The row is no longer ``failed`` — settled by the boot sweep, or by a stop —
        # so the reserve has been spent, the stand-in has joined, and this line is
        # dropped. The one outcome of the whole path that nothing else would report.
        await log_event(
            "WARNING",
            "neuroshilling_replay_dropped",
            account_id=account_id,
            extra={"target": target, "position": step.position},
        )
        return True
    text = step.text.strip()
    if not await _dispatch.vet_text(context, target, step, account_id, text):
        return True
    reply_to: int | None = None
    if speaker.sees_history:
        reply_to = await _dispatch.resolve_reply_to(context, target, step)
    elif step.reply_to_position is not None:
        await log_event(
            "WARNING",
            "neuroshilling_reply_anchor_lost",
            extra={"target": target, "position": step.position},
        )
    action = _dispatch.build_message_action(context.campaign, step, chat_id, text, reply_to)
    return await _dispatch.dispatch(context, target, step, account_id, action)


async def _reserve(
    context: RunContext,
    target: str,
    step: NeuroshillingStep,
    account_id: str,
    text: str,
) -> _Reserved:
    """Re-count the caps and insert the row, both under this account's lock.

    Everything between the count and the insert is inside the lock and there is no
    other await in it, which is what makes a serialised sibling see this row and stop
    at the cap instead of stacking past it.
    """
    async with account_lock(account_id):
        reason = await _quota_reason(context.campaign, account_id, target)
        claimed = await repository.claim_message(
            _key(context, target, step),
            campaign_id=context.campaign.campaign_id,
            account_id=account_id,
            text=text,
            # A refused step reserves its key through THIS insert rather than a second
            # one, so the resumed run treats it as played and the dialogue moves on.
            status="skipped" if reason else "pending",
        )
    return _Reserved(claimed=claimed, quota_reason=reason)


def _key(context: RunContext, target: str, step: NeuroshillingStep) -> NeuroshillingStepKey:
    return NeuroshillingStepKey(run_id=context.run_id, target=target, step_id=step.step_id)


async def _quota_reason(
    campaign: NeuroshillingCampaign,
    account_id: str,
    target: str,
) -> str | None:
    """Which of the campaign's three ceilings this account has reached, if any.

    Counted over MESSAGE steps only, which is what the operator's fields say: a
    reaction is not a message and gating one against ``messages_per_hour`` would make
    the number in the form mean something it does not say.
    """
    now = datetime.now(UTC)
    usage = await repository.read_quota_usage(
        campaign.campaign_id,
        account_id,
        target,
        hour_since=(now - timedelta(hours=1)).isoformat(),
        day_since=(now - timedelta(days=1)).isoformat(),
    )
    replies = await repository.count_chat_reply_usage(
        account_id,
        target,
        hour_since=(now - timedelta(hours=1)).isoformat(),
        day_since=(now - timedelta(days=1)).isoformat(),
        campaign_id=campaign.campaign_id,
    )
    pending_hour, pending_day, pending_total = pending_reply_usage(
        campaign.campaign_id, account_id, target
    )
    if usage.hour + replies.hour + pending_hour >= campaign.messages_per_hour:
        return "quota_hour"
    if 0 < campaign.messages_per_chat_per_day <= usage.chat_day + replies.chat_day + pending_day:
        return "quota_day"
    total = campaign.total_per_account
    if total is not None and usage.campaign_total + replies.campaign_total + pending_total >= total:
        return "quota_total"
    return None


async def _play_message(
    context: RunContext,
    target: str,
    step: NeuroshillingStep,
    account_id: str,
    chat_id: int,
) -> bool:
    """Reserve, vet, publish and settle one message step."""
    text = step.text.strip()
    carries_media = _dispatch.media_source(context.campaign, step) is not None
    if not text and not carries_media:
        return True
    reserved = await _reserve(context, target, step, account_id, text)
    if not reserved.claimed:
        return True
    if reserved.quota_reason is not None:
        await log_event(
            "WARNING",
            "neuroshilling_step_skipped_quota",
            account_id=account_id,
            extra={"target": target, "reason": reserved.quota_reason},
        )
        return True
    if not await _dispatch.vet_text(context, target, step, account_id, text):
        return True
    reply_to = await _dispatch.resolve_reply_to(context, target, step)
    action = _dispatch.build_message_action(context.campaign, step, chat_id, text, reply_to)
    return await _dispatch.dispatch(context, target, step, account_id, action)


async def _play_reaction(
    context: RunContext,
    target: str,
    step: NeuroshillingStep,
    account_id: str,
    chat_id: int,
) -> bool:
    """React to an earlier step's message. Skipped, never failed, when it cannot aim.

    The anchor is NOT walked up the chain the way a reply's is: a reply that lands one
    message higher still reads as the conversation it belongs to, whereas a reaction
    placed on a different message than the operator chose is simply the wrong reaction.
    No quota either — the ceilings are counted in messages, and this is not one.
    """
    anchor = context.by_position.get(step.target_position or 0)
    message_id = (
        None if anchor is None else await repository.fetch_message_id(_key(context, target, anchor))
    )
    # Looked up in the allowed set rather than compared against it, because the stored
    # column is free text: a row written before the set was narrowed must skip the step
    # rather than reach the gateway as an emoji it will refuse.
    emoji = next((allowed for allowed in _REACTIONS if allowed == step.emoji), None)
    if message_id is None or emoji is None:
        await log_event(
            "INFO",
            "neuroshilling_step_skipped_reaction",
            account_id=account_id,
            extra={"target": target, "position": step.position},
        )
        return True
    if not await repository.claim_message(
        _key(context, target, step),
        campaign_id=context.campaign.campaign_id,
        account_id=account_id,
        text="",
    ):
        return True
    action = ReactToMessage(chat_id=chat_id, message_id=message_id, emoji=emoji)
    return await _dispatch.dispatch(context, target, step, account_id, action)
