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
from core.repositories.neurocomment import set_campaign_status
from schemas.challenge import ChallengeRowList
from schemas.neurocomment import ChannelLinkOutcome
from services.neurocomment import _runtime

if TYPE_CHECKING:
    from schemas.challenge import ChallengeOutcomeCounts
    from schemas.neurocomment import (
        CampaignAccountList,
        CampaignChannelList,
        CampaignCreate,
        CampaignList,
        CampaignRunStatus,
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
    # A running listener must pick up the new channel now, not at the next restart.
    await _runtime.reconcile_if_running()
    return ChannelLinkOutcome(status="linked", channel=channel)


async def deactivate_channel(campaign_id: str, channel: str) -> None:
    """Free a channel from a campaign so its slot can move to another campaign."""
    await db.deactivate_channel(campaign_id, channel)
    # Drop the un-linked channel from a running listener's subscription immediately.
    await _runtime.reconcile_if_running()


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


async def list_campaign_challenges(campaign_id: str, limit: int) -> ChallengeRowList:
    """Recent non-solved challenges across a campaign's active channels (the captcha queue).

    Merges each active channel's drill-down (``list_failed_for_channel``), newest
    first, capped at ``limit``. A campaign has a handful of channels, so the
    per-channel fan-out is cheap and avoids a bespoke multi-channel query.
    """
    channel_links = await db.list_campaign_channels(campaign_id)
    merged = ChallengeRowList()
    for link in channel_links.links:
        if not link.active:
            continue
        result = await db.list_failed_for_channel(link.channel, limit)
        merged.rows.extend(result.rows)
    merged.rows.sort(key=lambda row: row.decided_at, reverse=True)
    return ChallengeRowList(rows=merged.rows[:limit])


async def count_challenge_outcomes(channels: list[str], since: str) -> ChallengeOutcomeCounts:
    """Header counters: challenge outcomes across a campaign's channels in a window (#148)."""
    return await db.count_by_outcome(channels, since)


async def count_campaign_challenge_outcomes(
    campaign_id: str,
    since: str,
) -> ChallengeOutcomeCounts:
    """Challenge-outcome counters for a campaign's active channels since ``since`` (#148).

    Resolves the campaign's active channels here (business logic) so the route stays a
    thin validate → call → serialize; delegates the grouped count to the repository.
    """
    channels = [
        link.channel for link in (await db.list_campaign_channels(campaign_id)).links if link.active
    ]
    return await db.count_by_outcome(channels, since)


async def set_solver_enabled(campaign_id: str, value: bool | None) -> None:  # noqa: FBT001 - tri-state value
    """Per-campaign solver switch: ``None`` follows the global flag, else force on/off (#148)."""
    await db.update_solver_enabled(campaign_id, value)


async def update_campaign_prompt(campaign_id: str, prompt: str) -> None:
    """Replace a campaign's generation prompt (the edit-prompt modal)."""
    await db.update_campaign_prompt(campaign_id, prompt)


async def set_status(campaign_id: str, status: CampaignRunStatus) -> None:
    """Per-campaign run/pause: persist the status and re-point a running listener.

    A paused campaign's channels leave the active watch set, so the engine both
    skips its posts (``fetch_active_campaign_for_channel`` filters ``status='active'``)
    and, once reconciled, stops watching them; resuming brings them back.
    """
    await set_campaign_status(campaign_id, status)
    await _runtime.reconcile_if_running()


async def skip_pair(account_id: str, channel: str) -> None:
    """Operator "Skip channel for this account": the engine never selects the pair (#148)."""
    await db.mark_human_skipped(account_id, channel)


async def delete_campaign(campaign_id: str) -> None:
    """Delete a campaign and clear all its account serving links, channels, and comments."""
    await db.delete_campaign(campaign_id)
