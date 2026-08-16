"""Scenario policy — read, replace, generate, approve.

The rules the repository has no opinion about: how long a scenario may be, which
links are legal, what a generation is allowed to cost, and — the one that matters
— what "approved" means.

**The approval gate is server-side and it is the whole point of this module.**
Only :func:`approve_scenario` writes ``scenario_status='approved'``, and it
validates the entire scenario first. Every write to the roles or steps returns the
campaign to ``draft`` inside the same transaction, and every campaign edit that
changes WHAT gets said does the same (``campaigns._resets_approval``). A later
stage refuses to launch a draft, so an approval that outlived the text it vouched
for would be the one bug that publishes unreviewed words into other people's chats.

A refusal carries its code and nothing else. The client holds the same roles and
steps this module validated and can point at the offending row itself, and the
error envelope has no way to carry a per-field list through ``HTTPException``
anyway.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.channel_tokens import parse_message_link
from core.config import settings
from core.logging import log_event
from core.repositories import neuroshilling as repository
from core.telegram_client import TelegramAccountNotFoundError, TelegramReadError
from schemas.neuroshilling_scenario import NeuroshillingScenario
from schemas.telegram_actions import ReadChatMessages, ReadChatMessagesResult
from schemas.telegram_actions_chat import COPYABLE_MEDIA_KINDS
from services.content import has_link, is_acceptable
from services.neuroshilling import _generate, _seams, _state
from services.neuroshilling._prompt import DialogueAsk
from services.neuroshilling.campaigns import (
    NeuroshillingConflictError,
    NeuroshillingInvalidError,
    NeuroshillingUnavailableError,
    refuse_while_live,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from schemas.neuroshilling import NeuroshillingCampaign, NeuroshillingRefusalCode
    from schemas.neuroshilling_scenario import (
        NeuroshillingGenerateRequest,
        NeuroshillingRole,
        NeuroshillingScenarioUpdate,
        NeuroshillingStep,
        NeuroshillingStepInput,
    )

_SCENARIO_INVALID: NeuroshillingRefusalCode = "scenario_invalid"
_LLM_UNAVAILABLE: NeuroshillingRefusalCode = "llm_unavailable"
_MEDIA_UNREACHABLE: NeuroshillingRefusalCode = "media_source_unreachable"
_MEDIA_CHECK_UNAVAILABLE: NeuroshillingRefusalCode = "media_check_unavailable"
# The approval problems specific enough to name on the wire. Everything else answers
# ``scenario_invalid``: the page holds the same roles and steps and can point at the
# offending row itself, whereas a refused LINE is not visibly wrong at all — and the
# two media ones name a scenario whose every row is legal, so "check the roles, steps
# and delays" would point away from the one field that is wrong.
_PROBLEM_CODES: dict[str, NeuroshillingRefusalCode] = {
    "text_has_link": "scenario_text_has_link",
    "text_forbidden_word": "scenario_text_forbidden_word",
    "media_step_not_message": "media_step_not_message",
    "media_step_missing": "media_step_missing",
}
# Read kinds that say nothing about what the account can SEE. A flood is Telegram
# pacing us and an ``unavailable`` is our own socket; both are over in minutes, and
# neither is evidence about the link.
_TRANSIENT_READ_KINDS = frozenset({"flood_wait", "unavailable"})


async def load_scenario(campaign_id: str) -> NeuroshillingScenario | None:
    """The scenario card's whole read. ``None`` means no such campaign."""
    campaign = await repository.fetch_campaign(campaign_id)
    if campaign is None:
        return None
    roles, steps = await repository.load_scenario(campaign_id)
    return NeuroshillingScenario(
        campaign_id=campaign_id,
        scenario_status=campaign.scenario_status,
        roles=roles,
        steps=steps,
    )


def _check_size(data: NeuroshillingScenarioUpdate) -> None:
    """Hold the body to the operator's configured ceilings.

    The schema's ``max_length`` is the bound nothing can raise; these are the
    tunables on top of it, which is why both exist.
    """
    limits = settings.neuroshilling
    if len(data.roles) > limits.max_roles or len(data.steps) > limits.max_steps:
        raise NeuroshillingInvalidError(_SCENARIO_INVALID)


def _backward_link_problem(steps: Sequence[NeuroshillingStepInput]) -> bool:
    """True when some step points at itself, forwards, or past the end.

    Positions are the array's own indices, so "contiguous" cannot be violated —
    the only thing left to check is direction, and direction is what makes a
    dialogue playable: the engine needs the message it is replying to to already
    exist in the chat.
    """
    return any(
        link is not None and not 1 <= link <= index
        for index, step in enumerate(steps)
        for link in (step.reply_to_position, step.target_position)
    )


def _kind_field_problem(steps: Sequence[NeuroshillingStepInput]) -> bool:
    """True when some step misses what its kind needs or carries the other kind's POSITION.

    ``_steps._play_reaction`` aims through ``target_position`` and fires ``emoji``,
    and when either is missing it logs INFO and returns success — the step is
    dropped and the run reports nothing wrong. ``_steps._play_message`` reads
    ``reply_to_position`` and never ``target_position``. So a reaction with no
    target or no emoji is a line the operator staged and the run silently skips,
    and a position filed under the other kind's name is one nothing reads back.

    The two POSITIONS are what is cross-checked, not every field. ``scenarioBody``
    files ``emoji`` and each link under its owning kind, and the one mismatch it
    does forward — ``text`` on a reaction — no control on the card can write, the
    text box being drawn for a message step only. Both are ignored where they land.

    Refused here rather than by the schema because the form reaches the first case
    on its own: ``addStep`` starts a reaction with no target and the target picker
    keeps a "none" entry. So it is an ordinary operator mistake, and it answers
    ``scenario_invalid`` beside the other save-time refusals rather than the
    generic ``validation_error`` ``api.errors`` returns for a schema rule.

    Generation never reaches this: :func:`generate_scenario` writes through
    ``repository.replace_scenario``. ``_generate._to_update`` is what keeps those
    rows inside this rule, and a test pins the two together.
    """
    return any(
        (step.target_position is None or step.emoji is None or step.reply_to_position is not None)
        if step.kind == "reaction"
        else step.target_position is not None
        for step in steps
    )


async def set_scenario(
    campaign_id: str,
    data: NeuroshillingScenarioUpdate,
) -> NeuroshillingScenario | None:
    """Replace roles and steps in one write. ``None`` means no such campaign.

    Always returns the campaign to ``draft`` — that is the repository's doing, in
    the same transaction, so it cannot be skipped by any caller.
    """
    campaign = await repository.fetch_campaign(campaign_id)
    if campaign is None:
        return None
    refuse_while_live(campaign)
    _check_size(data)
    if _backward_link_problem(data.steps) or _kind_field_problem(data.steps):
        raise NeuroshillingInvalidError(_SCENARIO_INVALID)
    if not await repository.replace_scenario(campaign_id, data.roles, data.steps):
        return None
    return await load_scenario(campaign_id)


def _approval_problem(
    campaign: NeuroshillingCampaign,
    roles: Sequence[NeuroshillingRole],
    steps: Sequence[NeuroshillingStep],
) -> str | None:
    """The first reason this scenario may not be approved, or ``None``.

    The reasons ``_PROBLEM_CODES`` does not list are for the test suite and the
    reader and answer the same ``scenario_invalid`` on the wire; the ones it lists
    are translated into refusals of their own.
    """
    if not roles:
        return "no_roles"
    if not any(step.kind == "message" for step in steps):
        return "no_message_step"
    if any(step.role_id is None for step in steps):
        return "step_without_role"
    return _text_problem(steps) or _media_problem(campaign, steps)


def _text_problem(steps: Sequence[NeuroshillingStep]) -> str | None:
    """Which outbound-filter rule the first unpublishable message step breaks.

    ``services.content.is_acceptable`` is run on every send by ``_dispatch``, over
    settings shared with warming — links blocked by default, and a forbidden-word list
    whose stock entries (``купить``, ``промокод``, ``скидк``) are the vocabulary a
    shilling dialogue is written in. Without this check a campaign was approved,
    launched, skipped EVERY message step and finished ``done`` with nothing sent, and
    the only trace was a warning per step in the log.

    The gate asked is ``is_acceptable`` itself, so this cannot drift from what the send
    path applies; the second read only decides which of its two rules to name.
    """
    for step in steps:
        if step.kind != "message" or is_acceptable(step.text):
            continue
        if settings.warming.content_block_links and has_link(step.text):
            return "text_has_link"
        return "text_forbidden_word"
    return None


def _media_problem(
    campaign: NeuroshillingCampaign,
    steps: Sequence[NeuroshillingStep],
) -> str | None:
    """The media slot must name a MESSAGE step that exists, or name nothing at all.

    The media travels as the message step's own send — ``_dispatch.media_source`` is
    consulted only where a MESSAGE goes out: ``_steps._play_message``, and the replay
    a stand-in makes of one. A reaction step under the slot therefore posts no media
    and logs nothing while doing it, so the run reaches ``done`` with the media never
    sent and no trace of why.

    Neither half needs anyone to choose it. The card's picker offers message steps
    only and its remove button moves the slot along with them, but no save endpoint
    reads the KIND under it, so an operator who turns that step into a reaction stores
    ``media_step_not_message`` — and ``media_step_missing`` is the ordinary state right
    after a generation, which clears the slot.

    Whether the accounts playing that step can actually SEE the source message is
    a Telegram question, answered by :func:`_refuse_unreachable_media` against live
    reads; this is the half that can be answered from the rows alone, so it runs
    first and a slot pointing at a reaction costs no reads to refuse.
    """
    position = campaign.media_step_position
    if campaign.media_message_link:
        if position is None or position > len(steps):
            return "media_step_missing"
        if steps[position - 1].kind != "message":
            return "media_step_not_message"
    elif position is not None:
        return "media_step_without_link"
    return None


async def _sees_media(account_id: str, chat: str, message_id: int) -> bool:
    """Whether ``account_id`` can read that message AND copy what it carries.

    Almost every failure is a "no". The account may be unknown, the chat unreachable,
    the message deleted or invisible, or the media a kind ``send_file`` cannot
    re-send — and the operator's next move is the same in all of them: point the
    campaign at a message its accounts can actually see.

    A flood wait or a dead socket is the exception, and it is not a "no" at all: the
    account was never asked. Answering ``False`` there told the operator to fix a
    link that was fine, so those two are raised as their own refusal — try again —
    rather than folded into a verdict on the link.
    """
    try:
        result = await _seams.execute_read(
            account_id,
            ReadChatMessages(chat=chat, message_ids=[message_id]),
        )
    except TelegramReadError as exc:
        if exc.kind in _TRANSIENT_READ_KINDS:
            raise NeuroshillingUnavailableError(_MEDIA_CHECK_UNAVAILABLE) from exc
        return False
    except TelegramAccountNotFoundError:
        return False
    if not isinstance(result, ReadChatMessagesResult) or not result.messages:
        return False
    return result.messages[0].media_kind in COPYABLE_MEDIA_KINDS


async def _refuse_unreachable_media(
    campaign: NeuroshillingCampaign,
    steps: Sequence[NeuroshillingStep],
) -> None:
    """Refuse approval when an account that must post the media cannot see it.

    The copy is made by the account playing the media step, not by some designated
    carrier: a message that arrives from an account with no part in the scene is
    exactly the tell the whole staging exists to avoid. So every account of that
    role has to be able to read the source, and that is a live Telegram question —
    which is why it is asked here, once, at approval, rather than discovered by the
    run in a stranger's chat.

    A link that cannot even be parsed is the same verdict: unreachable is
    unreachable, and a second code would only ask the operator to tell two versions
    of "fix this link" apart.
    """
    if not campaign.media_message_link or campaign.media_step_position is None:
        return
    link = parse_message_link(campaign.media_message_link)
    step = steps[campaign.media_step_position - 1]
    accounts = await repository.list_campaign_accounts(campaign.campaign_id)
    role_accounts = [
        account
        for account in accounts
        if account.role_id == step.role_id and account.state == "active"
    ]
    blind = (
        [account.account_id for account in role_accounts]
        if link is None
        else [
            account.account_id
            for account in role_accounts
            if not await _sees_media(account.account_id, link[0], link[1])
        ]
    )
    if not blind:
        return
    await log_event(
        "WARNING",
        "neuroshilling_media_unreachable",
        extra={"campaign_id": campaign.campaign_id, "accounts": len(blind)},
    )
    raise NeuroshillingInvalidError(_MEDIA_UNREACHABLE)


async def approve_scenario(campaign_id: str) -> NeuroshillingScenario | None:
    """Validate the stored scenario and mark it approved. ``None`` = no such campaign.

    Validates what is STORED rather than what a body claims: approval is a verdict
    on the rows the engine will read, and taking the client's word for their
    contents is exactly the gate this is.
    """
    campaign = await repository.fetch_campaign(campaign_id)
    if campaign is None:
        return None
    refuse_while_live(campaign)
    roles, steps = await repository.load_scenario(campaign_id)
    problem = _approval_problem(campaign, roles, steps)
    if problem is not None:
        raise NeuroshillingInvalidError(_PROBLEM_CODES.get(problem, _SCENARIO_INVALID))
    # After the row-only checks and never before them: this one talks to Telegram,
    # and a scenario that is broken on its own terms should not cost N live reads to
    # find out.
    await _refuse_unreachable_media(campaign, steps)
    if not await repository.approve_scenario(campaign_id):
        return None
    return await load_scenario(campaign_id)


async def generate_scenario(
    campaign_id: str,
    request: NeuroshillingGenerateRequest,
) -> NeuroshillingScenario | None:
    """Write a fresh dialogue with the LLM, replacing whatever is there.

    Overwriting is what the button means, and persisting rather than answering with
    an unsaved draft is what gives the roles and steps the ids the form needs to
    edit them.

    The budget claim wraps the WRITE as well as the call: released any earlier, a
    second click could start while the first was still storing its answer and the
    two would race over the same rows.
    """
    campaign = await repository.fetch_campaign(campaign_id)
    if campaign is None:
        return None
    refuse_while_live(campaign)
    _check_ask(request, campaign)
    refusal = _state.try_start_generation(campaign_id)
    if refusal is not None:
        raise NeuroshillingConflictError(refusal)
    try:
        draft = await _ask(campaign, request)
        if draft is None:
            raise NeuroshillingUnavailableError(_LLM_UNAVAILABLE)
        # The ASK is bounded, the answer is not: a model that returns more steps
        # than the operator's ceiling would otherwise be written straight past the
        # check the PUT makes, and the form could never save the campaign again.
        _check_size(draft)
        # The media slot is cleared by the same write. Every line is replaced, so
        # the position the operator picked now names text they have never read, and
        # :func:`_media_problem` cannot notice: it reads the KIND of the step there,
        # and a slot left on a step that is still a message passes.
        await repository.replace_scenario(
            campaign_id,
            draft.roles,
            draft.steps,
            clear_media_step=True,
        )
        # Re-read rather than composing from the row above: the write just moved
        # ``scenario_status`` back to ``draft`` and the stale copy still says
        # whatever it said before.
        return await load_scenario(campaign_id)
    finally:
        _state.finish_generation(campaign_id)


async def _ask(
    campaign: NeuroshillingCampaign,
    request: NeuroshillingGenerateRequest,
) -> NeuroshillingScenarioUpdate | None:
    """One generation under a wall-clock deadline. ``None`` is nothing usable.

    The deadline is what bounds the single-flight claim. Attempts times the
    gateway's own retries times its sixty-second timeout is half an hour in which
    every click on this campaign answers 409, and one hung socket must not be able
    to buy that.

    The stored role ids travel INTO the generation: the model knows nothing about
    the roles a campaign already has, so without them every generated role would
    be a new one, the old ones would be deleted, and the account roster's
    ``role_id`` — ``ON DELETE SET NULL`` — would come back empty.
    """
    roles, _steps = await repository.load_scenario(campaign.campaign_id)
    try:
        async with asyncio.timeout(settings.neuroshilling.llm_deadline_seconds):
            return await _generate.generate_dialogue(
                campaign.topic,
                DialogueAsk(
                    persona_count=request.persona_count,
                    step_count=request.step_count,
                    unique_messages=campaign.unique_messages,
                    # A revive campaign is briefed on the same topic but must
                    # sell nothing in it, so the mode reaches the prompt rather
                    # than only the engine.
                    revive=campaign.mode == "revive",
                ),
                role_ids=[role.role_id for role in roles],
            )
    except TimeoutError:
        return None


def _check_ask(request: NeuroshillingGenerateRequest, campaign: NeuroshillingCampaign) -> None:
    """Refuse an ask that could only produce something unusable — before it is paid for."""
    limits = settings.neuroshilling
    if request.persona_count > limits.max_roles or request.step_count > limits.max_steps:
        raise NeuroshillingInvalidError(_SCENARIO_INVALID)
    if not campaign.topic.strip():
        # A dialogue about nothing costs exactly as much as a dialogue about
        # something, and the operator's fix is one field away.
        raise NeuroshillingInvalidError(_SCENARIO_INVALID)
