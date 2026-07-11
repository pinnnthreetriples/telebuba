"""Account-side neurocomment queries: binding accounts to campaigns and pinning.

Split out of ``_campaigns.py`` to stay within the file-size budget. Public
functions wrap sync helpers via ``asyncio.to_thread`` and return Pydantic
models / ``None`` — never raw rows (non-negotiable #2).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.db import _get_engine, _now_iso
from core.repositories.neurocomment._tables import (
    _neurocomment_campaign_accounts,
    _neurocomment_campaign_channels,
)
from schemas.neurocomment import CampaignAccountLink, CampaignAccountList

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _assign_account_to_campaign(campaign_id: str, account_id: str) -> None:
    # on_conflict_do_nothing makes re-assignment idempotent while still letting a
    # foreign-key violation (unknown campaign/account) surface as IntegrityError.
    statement = (
        sqlite_insert(_neurocomment_campaign_accounts)
        .values(campaign_id=campaign_id, account_id=account_id, created_at=_now_iso())
        .on_conflict_do_nothing(
            index_elements=[
                _neurocomment_campaign_accounts.c.campaign_id,
                _neurocomment_campaign_accounts.c.account_id,
            ],
        )
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def assign_account_to_campaign(campaign_id: str, account_id: str) -> None:
    """Add an account to a campaign (idempotent). An account may serve many campaigns."""
    await asyncio.to_thread(_assign_account_to_campaign, campaign_id, account_id)


def _remove_account_from_campaign(campaign_id: str, account_id: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            delete(_neurocomment_campaign_accounts).where(
                (_neurocomment_campaign_accounts.c.campaign_id == campaign_id)
                & (_neurocomment_campaign_accounts.c.account_id == account_id),
            ),
        )


async def remove_account_from_campaign(campaign_id: str, account_id: str) -> None:
    """Drop an account↔campaign link (hard delete; idempotent if the link is absent).

    Scoped to the one ``(campaign_id, account_id)`` pair — an account serving other
    campaigns keeps those links, and per-``(account, channel)`` readiness (shared
    across campaigns) is untouched.
    """
    await asyncio.to_thread(_remove_account_from_campaign, campaign_id, account_id)


class ChannelNotInCampaignError(RuntimeError):
    """A pin target is not an active channel of the campaign."""


def _channel_is_active_in_campaign(connection: Connection, campaign_id: str, channel: str) -> bool:
    statement = select(_neurocomment_campaign_channels.c.id).where(
        (_neurocomment_campaign_channels.c.campaign_id == campaign_id)
        & (_neurocomment_campaign_channels.c.channel == channel)
        & (_neurocomment_campaign_channels.c.active == 1),
    )
    return connection.execute(statement).first() is not None


def _set_campaign_account_channel(campaign_id: str, account_id: str, channel: str | None) -> None:
    with _get_engine().begin() as connection:
        if channel is not None and not _channel_is_active_in_campaign(
            connection, campaign_id, channel
        ):
            msg = f"Channel {channel!r} is not active in campaign {campaign_id!r}"
            raise ChannelNotInCampaignError(msg)
        connection.execute(
            update(_neurocomment_campaign_accounts)
            .where(
                (_neurocomment_campaign_accounts.c.campaign_id == campaign_id)
                & (_neurocomment_campaign_accounts.c.account_id == account_id),
            )
            .values(channel=channel),
        )


async def set_campaign_account_channel(
    campaign_id: str,
    account_id: str,
    channel: str | None,
) -> None:
    """Pin a campaign account to one channel; ``None`` clears the pin (all channels).

    Raises ``ChannelNotInCampaignError`` when pinning to a channel that is not an
    active link of the campaign.
    """
    await asyncio.to_thread(_set_campaign_account_channel, campaign_id, account_id, channel)


def _list_campaign_accounts(campaign_id: str) -> CampaignAccountList:
    statement = (
        select(_neurocomment_campaign_accounts)
        .where(_neurocomment_campaign_accounts.c.campaign_id == campaign_id)
        .order_by(_neurocomment_campaign_accounts.c.created_at.asc())
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return CampaignAccountList(
        links=[CampaignAccountLink.model_validate(dict(row)) for row in rows],
    )


async def list_campaign_accounts(campaign_id: str) -> CampaignAccountList:
    return await asyncio.to_thread(_list_campaign_accounts, campaign_id)
