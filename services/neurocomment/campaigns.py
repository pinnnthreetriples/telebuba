"""Campaign setup operations for the neurocomment UI.

The service seam between the page and the repository, so features never import
``core.db`` / repositories directly (non-negotiables #1, #6). Most operations are thin
delegations; ``link_channel`` additionally converts the repository's
``ChannelAlreadyAssignedError`` into a typed :class:`ChannelLinkOutcome`, so the
exception never crosses into the UI layer (#2 — boundaries return models, not internals).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core import db
from schemas.neurocomment import ChannelLinkOutcome

if TYPE_CHECKING:
    from schemas.challenge import ChallengeRowList
    from schemas.neurocomment import (
        CampaignAccountList,
        CampaignChannelList,
        CampaignCreate,
        CampaignList,
        NeurocommentCampaign,
    )


async def create_campaign(data: CampaignCreate) -> NeurocommentCampaign:
    """Open a campaign (the product mention lives in its prompt)."""
    return await db.create_campaign(data)


async def list_campaigns() -> CampaignList:
    """Every campaign, oldest first."""
    return await db.list_campaigns()


async def list_campaign_channels(campaign_id: str) -> CampaignChannelList:
    """Active channel links for a campaign."""
    return await db.list_campaign_channels(campaign_id)


async def link_channel(campaign_id: str, channel: str) -> ChannelLinkOutcome:
    """Attach a channel to a campaign, reporting a uniqueness clash as a status.

    A channel can be the active target of only one campaign; if it is already taken the
    repository raises ``ChannelAlreadyAssignedError``, which is caught here and returned
    as ``already_assigned`` so the UI shows a message instead of handling an exception.
    """
    try:
        await db.link_channel_to_campaign(campaign_id, channel)
    except db.ChannelAlreadyAssignedError:
        return ChannelLinkOutcome(status="already_assigned", channel=channel)
    return ChannelLinkOutcome(status="linked", channel=channel)


async def deactivate_channel(campaign_id: str, channel: str) -> None:
    """Free a channel from a campaign so its slot can move to another campaign."""
    await db.deactivate_channel(campaign_id, channel)


async def list_campaign_accounts(campaign_id: str) -> CampaignAccountList:
    """Accounts assigned to serve a campaign."""
    return await db.list_campaign_accounts(campaign_id)


async def assign_account_to_campaign(campaign_id: str, account_id: str) -> None:
    """Add an account to a campaign's serving fleet (idempotent)."""
    await db.assign_account_to_campaign(campaign_id, account_id)


async def remove_account_from_campaign(campaign_id: str, account_id: str) -> None:
    """Remove an account from a campaign's serving fleet (idempotent)."""
    await db.remove_account_from_campaign(campaign_id, account_id)


async def list_channel_challenges(channel: str, limit: int) -> ChallengeRowList:
    """Recent non-solved challenges for a channel — the work-view drill-down (Ф2 #145)."""
    return await db.list_failed_for_channel(channel, limit)
