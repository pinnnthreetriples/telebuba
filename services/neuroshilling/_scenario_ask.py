"""The LLM ask behind Generate, and the refusals that make it not worth paying for."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.config import settings
from core.repositories import neuroshilling as repository
from services.neuroshilling import _generate
from services.neuroshilling._prompt import DialogueAsk
from services.neuroshilling.campaigns import NeuroshillingInvalidError

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingCampaign, NeuroshillingRefusalCode
    from schemas.neuroshilling_scenario import (
        NeuroshillingGenerateRequest,
        NeuroshillingScenarioUpdate,
    )

_SCENARIO_INVALID: NeuroshillingRefusalCode = "scenario_invalid"


async def ask(
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


def check_ask(request: NeuroshillingGenerateRequest, campaign: NeuroshillingCampaign) -> None:
    """Refuse an ask that could only produce something unusable — before it is paid for."""
    limits = settings.neuroshilling
    if request.persona_count > limits.max_roles or request.step_count > limits.max_steps:
        raise NeuroshillingInvalidError(_SCENARIO_INVALID)
    if not campaign.topic.strip():
        # A dialogue about nothing costs exactly as much as a dialogue about
        # something, and the operator's fix is one field away.
        raise NeuroshillingInvalidError(_SCENARIO_INVALID)
