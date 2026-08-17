"""Answering a real person: the decision, the request, and the four gates after it.

This is the only path in the project where text a stranger wrote decides what a
real account publishes, with no human in the loop. Everything about it is
arranged around that one fact.

**Two switches, and both must be on.** ``reply_to_humans`` says a run may answer
real people at all; ``autoresponder='neurodialog'`` says which engine writes the
answer. Either one alone publishes nothing. Both default to off.

**Nothing about the campaign is in the request.** ``_prompt.build_reply_prompt``
takes the observed chat and nothing else — no topic, no product, no persona, no
target list. What an injection can reach is therefore the conversation itself, and
our own scripted lines are part of that conversation: the product name is in them
if the operator's dialogue says it. That is not a leak, because those lines are
already public in the chat the attacker is standing in — but the brief behind them,
which is not, was never in the request to begin with.

**The answer is parsed before it is published**, by ``_reply_guard``, and then run
through the same ``is_acceptable`` / ``try_reserve_sent`` pair every other
publishing path in this project uses. The dedup reservation is scoped to the
target exactly as ``_dispatch`` scopes it, and it earns its place here for a
reason the scenario steps do not have: five accounts answering the same provoking
message would otherwise post five near-identical lines into one chat, which is
the cross-account duplicate signal ``services.content`` exists to suppress.

**A message is decided about once.** ``claim_chat_reply`` flips the row before the
model is asked and nothing gives it back, so a refused, filtered or failed answer
is never retried — a retry would pay for a second call on the same attacker text
and could publish on the second roll what the first one caught.

**A published answer is written into the chat log as ours, immediately.** It has no
journal row — the journal is keyed on a scenario step — so the id-based half of
``_listen``'s ownership test cannot see it, and a sibling account reading the chat
thirty seconds later would find our own reply looking exactly like a stranger's:
answered again, charged again, and re-entering the prompt labelled ``them``. The
one thing that would defeat is the ``us`` label ``_prompt`` documents as its reason
for tracking ownership at all.

The daily LLM budget and the per-campaign single-flight from the scenario generator
apply unchanged, and two ceilings on ATTEMPTS are added to them — one on the account's
hour, one on the chat's day. An autoresponder fires once per human message, so one busy
chat is otherwise a page of paid drafts every thirty seconds against a budget the whole
fleet shares, and the account's hour alone only spread that spend across the roster.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, NamedTuple

from core.config import settings
from core.logging import log_event
from core.repositories import neuroshilling as repository
from schemas.gemini import GeminiRequest
from schemas.neuroshilling import NeuroshillingChatMessage
from schemas.telegram_actions import PostComment
from services.content import is_acceptable, release_sent_text, try_reserve_sent
from services.neuroshilling import _dispatch, _prompt, _reply_guard, _seams, _state, _telegram

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingCampaign
    from services.neuroshilling._context import RunContext

# The two outcomes that must NOT give the dedup reservation back: one published the
# text, one may have.
_KEEPS_RESERVATION: Final = frozenset({"sent", "unconfirmed"})

_LLM_UNAVAILABLE: Final = "llm_unavailable"
_NO_ACCOUNT: Final = "no_account"
_QUOTA: Final = "quota"


class _Speaker(NamedTuple):
    """Who answers, and that account's OWN id for the chat.

    One name rather than two arguments because the two are meaningless apart: a
    chat id comes out of an account's own session entity cache, so pairing one with
    a different account is not a smaller mistake than omitting it.
    """

    account_id: str
    chat_id: int


def answering(campaign: NeuroshillingCampaign) -> bool:
    """May this campaign answer real people at all?

    Both switches, and the AND is the point rather than an accident: the operator
    who turns the autoresponder on to watch it draft answers has not thereby agreed
    to let a stranger's message steer what the fleet publishes.
    """
    return campaign.reply_to_humans and campaign.autoresponder == "neurodialog"


def _reply_chance(campaign: NeuroshillingCampaign) -> float:
    limits = settings.neuroshilling
    return {
        "calm": limits.reply_chance_calm,
        "medium": limits.reply_chance_medium,
        "active": limits.reply_chance_active,
    }[campaign.reply_activity]


async def _pick_account(context: RunContext, chats: dict[str, int]) -> str | None:
    """The least-busy account of this campaign that is inside this chat.

    The same load score the scenario steps pick by, and for the same reason: the
    ceiling being spread against is a rolling hour of the ACCOUNT's history, and
    letting whichever account happened to do the reading answer everything would
    concentrate a whole conversation on one session.

    Role-free, unlike the step picker: an autoreply belongs to no line of the
    dialogue, so any account that is present may write it.
    """
    candidates = [account_id for account_id in chats if account_id not in context.halted]
    if not candidates:
        return None
    since = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    counts = await repository.count_messages_since(candidates, since)
    fewest = min(counts.get(account_id, 0) for account_id in candidates)
    return _seams.rng.choice(
        [account_id for account_id in candidates if counts.get(account_id, 0) == fewest],
    )


async def _over_quota(campaign: NeuroshillingCampaign, account_id: str, target: str) -> bool:
    """Has this account, or this chat, reached a ceiling? Counting BOTH kinds of send.

    An autoreply has no journal row — the journal is keyed on a scenario step and an
    autoreply answers none — so the two counts are read separately and added. The
    numbers in the operator's form describe the account, and the account does not
    know which of our two code paths made it talk. All THREE are honoured, the
    lifetime one included: an autoreply does not add to ``campaign_total`` (it is not
    a campaign message) but it is still refused once the account's scenario steps have
    spent that allowance, or "total per account" would be a ceiling the account went
    on talking past for the rest of the run.

    The two ATTEMPT ceilings come first and cost no query: they are about what has
    already been PAID for rather than what has been published, and the two diverge
    exactly when a chat is hostile — every draft the gate refuses is billed and
    publishes nothing, so none of the three counted ceilings below ever sees it. The
    chat's is asked before the account's because it is the wider of the two: it is the
    chat that is charged the drafts, whichever of our accounts and campaigns paid.
    """
    if _state.at_chat_attempt_cap(target) or _state.at_reply_attempt_cap(
        account_id,
        campaign.messages_per_hour,
    ):
        return True
    now = datetime.now(UTC)
    hour_since = (now - timedelta(hours=1)).isoformat()
    day_since = (now - timedelta(days=1)).isoformat()
    journal = await repository.read_quota_usage(
        campaign.campaign_id,
        account_id,
        target,
        hour_since=hour_since,
        day_since=day_since,
    )
    replies = await repository.count_chat_reply_usage(
        account_id,
        target,
        hour_since=hour_since,
        day_since=day_since,
    )
    if journal.hour + replies.hour >= campaign.messages_per_hour:
        return True
    chat_day = journal.chat_day + replies.chat_day
    if 0 < campaign.messages_per_chat_per_day <= chat_day:
        return True
    total = campaign.total_per_account
    return total is not None and journal.campaign_total >= total


async def _refuse(account_id: str | None, target: str, reason: str) -> None:
    """One log line for an answer that will not be written or will not be sent.

    ``extra`` carries the target, the account and a stable code — never the chat
    message, never the model's answer. Both of those are attacker-controlled and
    ``GET /logs`` serves ``extra`` back as an HTTP response body.
    """
    await log_event(
        "WARNING",
        "neuroshilling_human_reply_rejected",
        account_id=account_id,
        extra={"target": target, "reason": reason},
    )


async def _draft(context: RunContext, target: str, message: NeuroshillingChatMessage) -> str | None:
    """Ask the model for one answer. ``None`` means nothing usable came back.

    The context read happens HERE and not at the poll, so the conversation quoted
    is the one as it stands at the moment of answering.
    """
    history = await repository.list_recent_chat(
        context.campaign.campaign_id,
        target,
        limit=settings.neuroshilling.chat_context_messages,
    )
    prompt = _prompt.build_reply_prompt(history, message)
    # Charged at the worst case and before the call, exactly as the scenario
    # generator charges it: the gateway retries a transient failure inside one call,
    # and a cap on spend must err high rather than low.
    _state.record_llm_call(calls=settings.deepseek.max_retries + 1)
    result = await _seams.generate_text_deepseek(
        GeminiRequest(
            api_key=settings.deepseek.api_key,
            prompt=prompt,
            model=settings.deepseek.model,
            temperature=settings.deepseek.temperature,
            max_output_tokens=settings.neuroshilling.llm_max_output_tokens,
        ),
    )
    return result.text if result.status == "ok" else None


async def _publish(
    context: RunContext,
    target: str,
    speaker: _Speaker,
    message: NeuroshillingChatMessage,
    text: str,
) -> None:
    """Send one vetted answer and record what became of it.

    The verdict goes through ``_telegram.record_send_verdict`` like a scenario
    step's, so a flood or a dead session earned while answering is persisted across
    the account's presence rows rather than being forgotten with this coroutine.

    A delivered answer is then written into the chat log as OURS, which is the row
    that stops the fleet answering itself: the send journal has no place to record an
    autoreply, so without this row a sibling account reads the line back as a
    stranger's, answers it, and its answer is read back in turn. The unique index
    makes the next poll's insert of the same message a no-op, and ``list_recent_chat``
    hands the line to the prompt labelled ``us``. It costs one thing, knowingly: the
    poll cursor is ``MAX(message_id)``, so a message posted by somebody else between
    the read and this send is now above the cursor and will not be read. That window
    is one send long, and the alternative is a bot talking to itself indefinitely.
    """
    account_id = speaker.account_id
    action = PostComment(chat_id=speaker.chat_id, text=text, reply_to=message.message_id)
    result = await _seams.execute(account_id, action)
    verdict = await _telegram.record_send_verdict(
        context.campaign.campaign_id,
        account_id,
        target,
        result,
    )
    if verdict in _telegram.HALTS_ACCOUNT:
        context.halted.add(account_id)
    if verdict not in _KEEPS_RESERVATION:
        # Nothing was published, so the dedup reservation this text took must go
        # back or the same wording is refused here for the whole dedup window.
        await release_sent_text(_dispatch.dedup_key(target, text))
        # Its own event, and the verdict under ``verdict`` rather than ``reason``:
        # this vocabulary is the gateway's, not the reply gate's, and ``_dispatch``
        # already reports it under that key.
        await log_event(
            "WARNING",
            "neuroshilling_human_reply_failed",
            account_id=account_id,
            extra={"target": target, "verdict": verdict},
        )
        return
    campaign_id = context.campaign.campaign_id
    await repository.record_chat_reply(
        campaign_id, target, message.message_id, account_id=account_id
    )
    if result.message_id:
        await repository.record_chat_messages(
            campaign_id,
            target,
            [NeuroshillingChatMessage(message_id=result.message_id, text=text, is_ours=True)],
        )
    await log_event(
        "INFO",
        "neuroshilling_human_reply_sent",
        account_id=account_id,
        extra={"target": target},
    )


async def consider(
    context: RunContext,
    target: str,
    chats: dict[str, int],
    message: NeuroshillingChatMessage,
) -> None:
    """Decide about one observed message and, if it wins every gate, answer it.

    The cheap tests come first and the paid one last: ownership, then the dice, then
    the key, then a speaker, then the ceilings, then the claim, and only then a model
    call. ``message.is_ours`` is checked here rather than by the caller because it is
    the one that must never be skipped — an account answering its own fleet is a loop
    with nothing outside it to stop it, and ``_publish`` writing our own answers into
    the chat log is what makes the flag true for them.
    """
    campaign = context.campaign
    if message.is_ours or not message.text.strip() or not answering(campaign):
        return
    if _seams.rng.random() >= _reply_chance(campaign):
        # A group where every message gets an answer within a minute is not a group
        # with people in it. The roll is per message and deliberately before the
        # claim, so an unanswered message stays open to nothing at all.
        return
    if not settings.deepseek.api_key:
        # Behind the dice and logged once per process, because a missing key is one
        # fact about the deployment: in front of the dice it was a WARNING row per
        # observed message, which one busy chat turns into four figures an hour.
        if _state.first_key_warning(campaign.campaign_id):
            await _refuse(None, target, _LLM_UNAVAILABLE)
        return
    account_id = await _pick_account(context, chats)
    if account_id is None:
        await _refuse(None, target, _NO_ACCOUNT)
        return
    if await _over_quota(campaign, account_id, target):
        await _refuse(account_id, target, _QUOTA)
        return
    if not await repository.claim_chat_reply(campaign.campaign_id, target, message.message_id):
        return
    # Charged where the claim is taken and not where the request is made: everything
    # from here on is paid for, including the drafts the gate throws away.
    _state.record_reply_attempt(account_id, target)
    await _answer(context, target, _Speaker(account_id, chats[account_id]), message)


async def _answer(
    context: RunContext,
    target: str,
    speaker: _Speaker,
    message: NeuroshillingChatMessage,
) -> None:
    """Everything after the claim: the budget, the call, the gates, the send."""
    account_id = speaker.account_id
    refusal = _state.try_start_generation(context.campaign.campaign_id)
    if refusal is not None:
        # The scenario generator's own single-flight and daily budget, reused whole.
        # An autoresponder fires once per human message, so it is unbounded by
        # construction and needs the same ceiling a click-driven generation has.
        await _refuse(account_id, target, refusal)
        return
    try:
        candidate = await _draft(context, target, message)
    finally:
        _state.finish_generation(context.campaign.campaign_id)
    if candidate is None:
        await _refuse(account_id, target, _LLM_UNAVAILABLE)
        return
    verdict = _reply_guard.clean_reply(candidate, message.text)
    if verdict.text is None:
        await _refuse(account_id, target, verdict.reason or _LLM_UNAVAILABLE)
        return
    if not is_acceptable(verdict.text):
        await _refuse(account_id, target, "not_acceptable")
        return
    if not await try_reserve_sent(_dispatch.dedup_key(target, verdict.text)):
        await _refuse(account_id, target, "duplicate")
        return
    await _publish(context, target, speaker, message, verdict.text)
