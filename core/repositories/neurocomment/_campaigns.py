"""Campaign-side neurocomment queries: campaigns, channel binding, account binding."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from core.db import _get_engine, _now_iso
from core.repositories.neurocomment._tables import (
    _neurocomment_campaign_accounts,
    _neurocomment_campaign_channels,
    _neurocomment_campaigns,
    _neurocomment_comments,
)
from schemas.neurocomment import (
    CampaignChannelLink,
    CampaignChannelList,
    CampaignCreate,
    CampaignList,
    ChannelList,
    NeurocommentCampaign,
)

if TYPE_CHECKING:
    from sqlalchemy import Join, RowMapping
    from sqlalchemy.engine import Connection


def _row_to_campaign(row: RowMapping) -> NeurocommentCampaign:
    return NeurocommentCampaign.model_validate(dict(row))


def _create_campaign(data: CampaignCreate) -> NeurocommentCampaign:
    now = _now_iso()
    campaign_id = uuid4().hex
    with _get_engine().begin() as connection:
        connection.execute(
            insert(_neurocomment_campaigns).values(
                campaign_id=campaign_id,
                name=data.name,
                prompt=data.prompt,
                status=data.status,
                created_at=now,
                updated_at=now,
            ),
        )
    campaign = _fetch_campaign(campaign_id)
    if campaign is None:  # pragma: no cover - insert above guarantees the row
        msg = f"Campaign was not persisted: {campaign_id}"
        raise RuntimeError(msg)
    return campaign


async def create_campaign(data: CampaignCreate) -> NeurocommentCampaign:
    """Open a new campaign with a generated ``campaign_id``."""
    return await asyncio.to_thread(_create_campaign, data)


def _fetch_campaign(campaign_id: str) -> NeurocommentCampaign | None:
    statement = select(_neurocomment_campaigns).where(
        _neurocomment_campaigns.c.campaign_id == campaign_id,
    )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    return None if row is None else _row_to_campaign(row)


async def fetch_campaign(campaign_id: str) -> NeurocommentCampaign | None:
    return await asyncio.to_thread(_fetch_campaign, campaign_id)


def _active_channel_counts(connection: Connection) -> dict[str, int]:
    """Per-campaign active-channel counts in one grouped query (no per-campaign loop)."""
    statement = (
        select(
            _neurocomment_campaign_channels.c.campaign_id,
            func.count().label("n"),
        )
        .where(_neurocomment_campaign_channels.c.active == 1)
        .group_by(_neurocomment_campaign_channels.c.campaign_id)
    )
    return {str(cid): int(n) for cid, n in connection.execute(statement).all()}


def _account_counts(connection: Connection) -> dict[str, int]:
    """Per-campaign serving-account counts in one grouped query (no per-campaign loop)."""
    statement = select(
        _neurocomment_campaign_accounts.c.campaign_id,
        func.count().label("n"),
    ).group_by(_neurocomment_campaign_accounts.c.campaign_id)
    return {str(cid): int(n) for cid, n in connection.execute(statement).all()}


def _list_campaigns() -> CampaignList:
    statement = select(_neurocomment_campaigns).order_by(
        _neurocomment_campaigns.c.created_at.asc(),
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
        channel_counts = _active_channel_counts(connection)
        account_counts = _account_counts(connection)
    campaigns = []
    for row in rows:
        campaign = _row_to_campaign(row)
        campaign.channel_count = channel_counts.get(campaign.campaign_id, 0)
        campaign.account_count = account_counts.get(campaign.campaign_id, 0)
        campaigns.append(campaign)
    return CampaignList(campaigns=campaigns)


async def list_campaigns() -> CampaignList:
    return await asyncio.to_thread(_list_campaigns)


def _update_solver_enabled(campaign_id: str, value: bool | None) -> None:  # noqa: FBT001 - tri-state value
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_campaigns)
            .where(_neurocomment_campaigns.c.campaign_id == campaign_id)
            .values(solver_enabled=value, updated_at=_now_iso()),
        )


async def update_solver_enabled(campaign_id: str, value: bool | None) -> None:  # noqa: FBT001 - tri-state value
    """Set the per-campaign challenge-solver override (``None`` = follow the global flag)."""
    await asyncio.to_thread(_update_solver_enabled, campaign_id, value)


def _set_campaign_status(campaign_id: str, status: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_campaigns)
            .where(_neurocomment_campaigns.c.campaign_id == campaign_id)
            .values(status=status, updated_at=_now_iso()),
        )


async def set_campaign_status(campaign_id: str, status: str) -> None:
    """Set a campaign's lifecycle status (active/paused/archived) for per-campaign run/pause."""
    await asyncio.to_thread(_set_campaign_status, campaign_id, status)


def _update_campaign_prompt(campaign_id: str, prompt: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_campaigns)
            .where(_neurocomment_campaigns.c.campaign_id == campaign_id)
            .values(prompt=prompt, updated_at=_now_iso()),
        )


async def update_campaign_prompt(campaign_id: str, prompt: str) -> None:
    """Replace a campaign's generation prompt (the edit-prompt modal)."""
    await asyncio.to_thread(_update_campaign_prompt, campaign_id, prompt)


class ChannelAlreadyAssignedError(RuntimeError):
    """A channel is already active in another campaign (the one-active invariant)."""


def _row_to_channel_link(row: RowMapping) -> CampaignChannelLink:
    return CampaignChannelLink.model_validate(dict(row))


def _active_channel_link(connection: Connection, channel: str) -> CampaignChannelLink | None:
    statement = select(_neurocomment_campaign_channels).where(
        (_neurocomment_campaign_channels.c.channel == channel)
        & (_neurocomment_campaign_channels.c.active == 1),
    )
    row = connection.execute(statement).mappings().first()
    return None if row is None else _row_to_channel_link(row)


def _link_channel_to_campaign(campaign_id: str, channel: str) -> CampaignChannelLink:
    try:
        with _get_engine().begin() as connection:
            connection.execute(
                insert(_neurocomment_campaign_channels).values(
                    campaign_id=campaign_id,
                    channel=channel,
                    active=1,
                    created_at=_now_iso(),
                ),
            )
            link = _active_channel_link(connection, channel)
    except IntegrityError:
        with _get_engine().connect() as connection:
            existing = _active_channel_link(connection, channel)
        if existing is not None:
            msg = f"Channel {channel!r} is already active in campaign {existing.campaign_id!r}"
            raise ChannelAlreadyAssignedError(msg) from None
        raise
    if link is None:  # pragma: no cover - the insert above guarantees the row
        msg = f"Channel link was not persisted: {channel!r}"
        raise RuntimeError(msg)
    return link


async def link_channel_to_campaign(campaign_id: str, channel: str) -> CampaignChannelLink:
    """Bind a channel to a campaign as active.

    Raises ``ChannelAlreadyAssignedError`` if the channel is already active in any
    campaign (the DB partial-unique index is the source of truth).
    """
    return await asyncio.to_thread(_link_channel_to_campaign, campaign_id, channel)


def _deactivate_channel(campaign_id: str, channel: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_campaign_channels)
            .where(
                (_neurocomment_campaign_channels.c.campaign_id == campaign_id)
                & (_neurocomment_campaign_channels.c.channel == channel)
                & (_neurocomment_campaign_channels.c.active == 1),
            )
            .values(active=0),
        )
        # Clear any pin to this now-inactive channel: a pin to a channel no longer
        # active would silently exclude the account from selection + onboarding forever.
        connection.execute(
            update(_neurocomment_campaign_accounts)
            .where(
                (_neurocomment_campaign_accounts.c.campaign_id == campaign_id)
                & (_neurocomment_campaign_accounts.c.channel == channel),
            )
            .values(channel=None),
        )


async def deactivate_channel(campaign_id: str, channel: str) -> None:
    """Free a channel from a campaign — its active link becomes inactive."""
    await asyncio.to_thread(_deactivate_channel, campaign_id, channel)


def _list_campaign_channels(campaign_id: str) -> CampaignChannelList:
    statement = (
        select(_neurocomment_campaign_channels)
        .where(
            (_neurocomment_campaign_channels.c.campaign_id == campaign_id)
            & (_neurocomment_campaign_channels.c.active == 1),
        )
        .order_by(_neurocomment_campaign_channels.c.id.asc())
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return CampaignChannelList(links=[_row_to_channel_link(row) for row in rows])


async def list_campaign_channels(campaign_id: str) -> CampaignChannelList:
    """Active channel links for a campaign."""
    return await asyncio.to_thread(_list_campaign_channels, campaign_id)


def _active_channel_join() -> Join:
    """Join: a channel link to its campaign (filter active/status at the call site)."""
    return _neurocomment_campaign_channels.join(
        _neurocomment_campaigns,
        _neurocomment_campaign_channels.c.campaign_id == _neurocomment_campaigns.c.campaign_id,
    )


def _fetch_active_campaign_for_channel(channel: str) -> NeurocommentCampaign | None:
    statement = (
        select(_neurocomment_campaigns)
        .select_from(_active_channel_join())
        .where(
            (_neurocomment_campaign_channels.c.channel == channel)
            & (_neurocomment_campaign_channels.c.active == 1)
            & (_neurocomment_campaigns.c.status == "active"),
        )
    )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    return None if row is None else _row_to_campaign(row)


async def fetch_active_campaign_for_channel(channel: str) -> NeurocommentCampaign | None:
    """The active campaign whose active link == ``channel``; ``None`` if none.

    Maps a fresh post back to its campaign + prompt on the engine hot path.
    """
    return await asyncio.to_thread(_fetch_active_campaign_for_channel, channel)


def _list_active_watch_channels() -> ChannelList:
    statement = (
        select(_neurocomment_campaign_channels.c.channel)
        .select_from(_active_channel_join())
        .where(
            (_neurocomment_campaign_channels.c.active == 1)
            & (_neurocomment_campaigns.c.status == "active"),
        )
        .distinct()
        .order_by(_neurocomment_campaign_channels.c.channel.asc())
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).scalars().all()
    return ChannelList(channels=list(rows))


async def list_active_watch_channels() -> ChannelList:
    """All channels with an active link in an active campaign — the listener watch set."""
    return await asyncio.to_thread(_list_active_watch_channels)


def _delete_campaign(campaign_id: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            delete(_neurocomment_campaign_accounts).where(
                _neurocomment_campaign_accounts.c.campaign_id == campaign_id,
            ),
        )
        connection.execute(
            delete(_neurocomment_campaign_channels).where(
                _neurocomment_campaign_channels.c.campaign_id == campaign_id,
            ),
        )
        connection.execute(
            delete(_neurocomment_comments).where(
                _neurocomment_comments.c.campaign_id == campaign_id,
            ),
        )
        connection.execute(
            delete(_neurocomment_campaigns).where(
                _neurocomment_campaigns.c.campaign_id == campaign_id,
            ),
        )


async def delete_campaign(campaign_id: str) -> None:
    """Delete a campaign and clear all its account serving links, channels, and comments."""
    await asyncio.to_thread(_delete_campaign, campaign_id)
