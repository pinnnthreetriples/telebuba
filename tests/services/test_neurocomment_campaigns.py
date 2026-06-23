"""Tests for ``services.neurocomment.campaigns`` — the page→repository service seam."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import configure_database, create_account
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from services.neurocomment import campaigns

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


@pytest.mark.asyncio
async def test_create_and_list_campaigns() -> None:
    created = await campaigns.create_campaign(CampaignCreate(name="Promo", prompt="p"))
    assert created.name == "Promo"
    listed = await campaigns.list_campaigns()
    assert [c.campaign_id for c in listed.campaigns] == [created.campaign_id]


@pytest.mark.asyncio
async def test_link_channel_reports_clash_instead_of_raising() -> None:
    a = await campaigns.create_campaign(CampaignCreate(name="A", prompt="p"))
    b = await campaigns.create_campaign(CampaignCreate(name="B", prompt="p"))

    first = await campaigns.link_channel(a.campaign_id, "@chan")
    assert first.status == "linked"
    assert first.channel == "@chan"
    channels = await campaigns.list_campaign_channels(a.campaign_id)
    assert [link.channel for link in channels.links] == ["@chan"]

    # The channel is the active target of A → linking it to B is reported, not raised.
    clash = await campaigns.link_channel(b.campaign_id, "@chan")
    assert clash.status == "already_assigned"

    # Freeing it from A lets B take it.
    await campaigns.deactivate_channel(a.campaign_id, "@chan")
    moved = await campaigns.link_channel(b.campaign_id, "@chan")
    assert moved.status == "linked"


@pytest.mark.asyncio
async def test_assign_and_remove_account() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await campaigns.create_campaign(CampaignCreate(name="A", prompt="p"))

    await campaigns.assign_account_to_campaign(campaign.campaign_id, "acc-1")
    assigned = await campaigns.list_campaign_accounts(campaign.campaign_id)
    assert [link.account_id for link in assigned.links] == ["acc-1"]

    await campaigns.remove_account_from_campaign(campaign.campaign_id, "acc-1")
    assert (await campaigns.list_campaign_accounts(campaign.campaign_id)).links == []
