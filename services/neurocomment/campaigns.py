"""Campaign setup operations for the neurocomment UI.

The service seam between the page and the repository, so features never import
``core.db`` / repositories directly (non-negotiables #1, #6). Most operations are thin
delegations; ``link_channel`` additionally converts the repository's
``ChannelAlreadyAssignedError`` into a typed :class:`ChannelLinkOutcome`, so the
exception never crosses into the UI layer (#2 — boundaries return models, not internals).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core import db
from core.config import settings
from core.repositories.neurocomment import (
    set_campaign_account_channels,
    set_campaign_status,
)
from schemas.challenge import AccountChannel
from schemas.neurocomment import ChannelLinkOutcome
from services.neurocomment import _discovery_state, _rejoin, _runtime, _state
from services.neurocomment.board import load_neurocomment_board

if TYPE_CHECKING:
    from schemas.challenge import ChallengeOutcomeCounts, ChallengeRowList
    from schemas.neurocomment import (
        CampaignAccountList,
        CampaignChannelList,
        CampaignCreate,
        CampaignList,
        CampaignRunStatus,
        NeurocommentBoard,
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
    # A running listener onboards the new account now, not at the next Start.
    await _runtime.reconcile_if_running()


async def remove_account_from_campaign(campaign_id: str, account_id: str) -> None:
    """Remove an account from a campaign's serving fleet (idempotent)."""
    await db.remove_account_from_campaign(campaign_id, account_id)


async def list_channel_challenges(channel: str, limit: int) -> ChallengeRowList:
    """Recent non-solved challenges for a channel — the work-view drill-down (Ф2 #145)."""
    return await db.list_failed_for_channel(channel, limit)


async def _rejoin_exhausted_pairs() -> list[AccountChannel]:
    """Pairs out of their chat whose re-join budget is spent — the live report's six.

    Read off ``_rejoin``'s own two predicates rather than re-derived here, so the queue and
    the re-join rule cannot disagree about who is finished — the same pairing ``board``
    badges ``join_failed`` with. A pair still inside its budget is NOT here: the sweep is
    working on it, a retry may well land it back in the chat, and its captcha then matters
    again.

    Nothing acts on this list any more — since #49 the queue is a read-only view of what the
    automatic rules are working on — so the exclusion is now about honesty rather than harm:
    a pair the re-join rule has finished with is not being worked on, and saying it is makes
    the panel lie. (It used to be about harm too. Every row carried a «Повторить» button
    whose join RPC Telegram had already refused four times, and which — by deleting the
    readiness row — handed the pair a fresh budget the rule had spent four days ending.)
    """
    return [
        AccountChannel(account_id=row.account_id, channel=row.channel)
        for row in (await db.list_access_lost_readiness()).readiness
        if _rejoin.access_lost(row) and _rejoin.exhausted(row)
    ]


async def list_campaign_challenges(campaign_id: str, limit: int) -> ChallengeRowList:
    """Actionable non-solved challenges across a campaign's active channels (the captcha queue).

    One repository query over the campaign's active channels, newest first, capped at
    ``limit`` — replaces the former per-channel fan-out (one query per channel).

    Since #49 the view holds no control at all: the captcha rule retries and gives up on its
    own, so a listed row is a claim that a pair is being worked on right now, and every
    exclusion below is that claim being kept true. Four states go, each for its own reason,
    and a row that qualifies is hidden rather than greyed out — a badged-and-dead row is a
    second list needing its own triage:

    * banned, operator-skipped, or given up on the captcha — the rules are finished with the
      pair (``_retry_can_reach``);
    * the re-join budget is spent — the pair is out of the chat and no rule will put it back
      (:func:`_rejoin_exhausted_pairs`);
    * the channel is serving out a #147 pause — ``_join_and_classify`` returns
      ``channel_paused`` before any join RPC, reading the very column carried on the link
      below, so nothing can happen for this pair until the window lapses. Dropped for its
      duration only, exactly as ``_rejoin._review_channel`` sits a pause out, and the rows
      come back afterwards because a pause is shorter than the age window.

    A paused channel drops out of the channel list and the budget goes in as an exclusion
    list, so every rule is inside the one statement the database applies ``limit`` to. That
    matters more than it looks: the queue lists challenge ROWS, and one pair collects a new
    one on every pass that meets the guardian bot, so six finished pairs are easily 24 rows.
    Filtered after the fact they would fill a 20-row queue and hide the one pair a human
    could still act on — the same blindness in a new place.
    """
    now = datetime.now(UTC)
    channels = [
        link.channel
        for link in (await db.list_campaign_channels(campaign_id)).links
        if link.active and not _state.channel_paused(link.paused_until, now)
    ]
    since = (now - timedelta(days=settings.neurocomment.challenge_queue_max_age_days)).isoformat()
    return await db.list_failed_for_channels(
        channels,
        limit,
        since,
        await _rejoin_exhausted_pairs() if channels else [],
    )


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


async def set_account_channels(
    campaign_id: str,
    account_id: str,
    channels: list[str],
) -> NeurocommentBoard | None:
    """Set a campaign account's channel subset (empty = all channels); return the board.

    Raises ``ChannelNotInCampaignError`` when any channel is not an active channel of
    the campaign, so the route can map it to a 400 instead of leaking a repo internal.
    Onboarding after a subset change is operator-driven (Start), matching the existing
    solver-toggle behaviour; selection immediately honours the new subset on the next post.
    """
    await set_campaign_account_channels(campaign_id, account_id, channels)
    return await load_neurocomment_board(campaign_id)


async def delete_campaign(campaign_id: str) -> None:
    """Delete a campaign and clear all its account serving links, channels, and comments."""
    # First: a discovery run would otherwise keep probing the shared listener for
    # minutes on behalf of rows this delete is about to remove, writing to nothing.
    _discovery_state.cancel_campaign_run(campaign_id)
    await db.delete_campaign(campaign_id)
