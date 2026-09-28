"""One validated Save for the settings form and its dialogue."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.repositories import neuroshilling as repository
from services.neuroshilling.campaigns import (
    _CAMPAIGN_CHANGED,
    _UNKNOWN_ROLE,
    NeuroshillingConflictError,
    NeuroshillingInvalidError,
    _check_shape,
    _existing_account_ids,
    campaign_edit,
    refuse_while_live,
)
from services.neuroshilling.scenario import (
    _SCENARIO_INVALID,
    _backward_link_problem,
    _check_size,
    _kind_field_problem,
)

if TYPE_CHECKING:
    from schemas.neuroshilling_scenario import NeuroshillingSettings, NeuroshillingSettingsUpdate


async def load_settings(campaign_id: str) -> NeuroshillingSettings | None:
    return await repository.load_settings(campaign_id)


async def save_settings(
    campaign_id: str, data: NeuroshillingSettingsUpdate
) -> NeuroshillingSettings | None:
    """Validate both forms, then commit campaign, roster and dialogue together."""
    with campaign_edit(campaign_id):
        campaign = await repository.fetch_campaign(campaign_id)
        if campaign is None:
            return None
        refuse_while_live(campaign)
        if campaign.updated_at != data.campaign.expected_updated_at:
            raise NeuroshillingConflictError(_CAMPAIGN_CHANGED)
        _check_shape(data.campaign)
        _check_size(data.scenario)
        if _backward_link_problem(data.scenario.steps) or _kind_field_problem(data.scenario.steps):
            raise NeuroshillingInvalidError(_SCENARIO_INVALID)
        existing_accounts = await _existing_account_ids()
        data.campaign.accounts = [
            item for item in data.campaign.accounts if item.account_id in existing_accounts
        ]
        stored_roles = await repository.list_campaign_role_ids(campaign_id)
        incoming_roles = {role.role_id for role in data.scenario.roles if role.role_id is not None}
        named = {item.role_id for item in data.campaign.accounts if item.role_id is not None}
        if not named <= stored_roles | incoming_roles:
            raise NeuroshillingInvalidError(_UNKNOWN_ROLE)
        saved = await repository.save_settings(campaign_id, data)
        if saved is None and await repository.fetch_campaign(campaign_id) is not None:
            raise NeuroshillingConflictError(_CAMPAIGN_CHANGED)
        return saved
