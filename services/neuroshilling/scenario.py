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

from core.config import settings
from core.repositories import neuroshilling as repository
from schemas.neuroshilling_scenario import NeuroshillingScenario
from services.neuroshilling import _generate, _state
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
    if _backward_link_problem(data.steps):
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

    The reason is for the test suite and the reader, not for the wire: every one of
    them answers the same ``scenario_invalid``.
    """
    if not roles:
        return "no_roles"
    if not any(step.kind == "message" for step in steps):
        return "no_message_step"
    if any(step.role_id is None for step in steps):
        return "step_without_role"
    return _media_problem(campaign, len(steps))


def _media_problem(campaign: NeuroshillingCampaign, step_count: int) -> str | None:
    """The media slot must name a step that exists, or name nothing at all.

    Whether the accounts playing that step can actually SEE the source message is
    a Telegram question and belongs to the stage that adds the read; this is the
    half that can be answered from the rows alone.
    """
    if campaign.media_message_link:
        if campaign.media_step_position is None or campaign.media_step_position > step_count:
            return "media_step_missing"
    elif campaign.media_step_position is not None:
        return "media_step_without_link"
    return None


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
    if _approval_problem(campaign, roles, steps) is not None:
        raise NeuroshillingInvalidError(_SCENARIO_INVALID)
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
        await repository.replace_scenario(campaign_id, draft.roles, draft.steps)
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
                persona_count=request.persona_count,
                step_count=request.step_count,
                unique_messages=campaign.unique_messages,
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
