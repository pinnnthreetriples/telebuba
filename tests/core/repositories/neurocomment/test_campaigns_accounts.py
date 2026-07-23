"""Neurocomment campaign and account-assignment repository tests."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from core.db import (  # type: ignore[attr-defined]
    ChannelAlreadyAssignedError,
    assign_account_to_campaign,
    create_account,
    create_campaign,
    deactivate_channel,
    fetch_active_campaign_for_channel,
    fetch_campaign,
    link_channel_to_campaign,
    list_active_watch_channels,
    list_campaign_accounts,
    list_campaign_channels,
    list_campaigns,
    remove_account_from_campaign,
    update_campaign_prompt,
    update_solver_enabled,
)
from core.repositories.neurocomment import (
    ChannelNotInCampaignError,
    set_campaign_account_channels,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate


@pytest.mark.asyncio
async def test_update_solver_enabled_roundtrips_through_fetch() -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    assert campaign.solver_enabled is None  # default: follow the global flag

    async def _solver_enabled() -> bool | None:
        fetched = await fetch_campaign(campaign.campaign_id)
        assert fetched is not None
        return fetched.solver_enabled

    await update_solver_enabled(campaign.campaign_id, value=True)
    assert await _solver_enabled() is True
    await update_solver_enabled(campaign.campaign_id, value=False)
    assert await _solver_enabled() is False
    await update_solver_enabled(campaign.campaign_id, value=None)
    assert await _solver_enabled() is None


@pytest.mark.asyncio
async def test_create_campaign_then_fetch_and_list() -> None:
    created = await create_campaign(CampaignCreate(name="Promo", prompt="mention X subtly"))
    assert created.name == "Promo"
    assert created.prompt == "mention X subtly"
    assert created.status == "active"
    assert created.campaign_id

    fetched = await fetch_campaign(created.campaign_id)
    assert fetched is not None
    assert fetched.campaign_id == created.campaign_id
    assert await fetch_campaign("does-not-exist") is None

    listed = await list_campaigns()
    assert [c.campaign_id for c in listed.campaigns] == [created.campaign_id]


@pytest.mark.asyncio
async def test_channel_belongs_to_one_active_campaign() -> None:
    a = await create_campaign(CampaignCreate(name="A", prompt="p"))
    b = await create_campaign(CampaignCreate(name="B", prompt="p"))

    link = await link_channel_to_campaign(a.campaign_id, "@chan")
    assert link.campaign_id == a.campaign_id
    assert link.channel == "@chan"
    assert link.active is True

    with pytest.raises(ChannelAlreadyAssignedError):
        await link_channel_to_campaign(b.campaign_id, "@chan")

    channels = await list_campaign_channels(a.campaign_id)
    assert [link.channel for link in channels.links] == ["@chan"]

    # freeing the slot in A lets the channel move to B
    await deactivate_channel(a.campaign_id, "@chan")
    relinked = await link_channel_to_campaign(b.campaign_id, "@chan")
    assert relinked.campaign_id == b.campaign_id
    assert [link.channel for link in (await list_campaign_channels(a.campaign_id)).links] == []


@pytest.mark.asyncio
async def test_link_channel_to_unknown_campaign_raises_integrity_error() -> None:
    # FK violation is distinct from the uniqueness conflict and must propagate.
    with pytest.raises(IntegrityError):
        await link_channel_to_campaign("ghost-campaign", "@chan")


@pytest.mark.asyncio
async def test_account_serves_many_campaigns() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    a = await create_campaign(CampaignCreate(name="A", prompt="p"))
    b = await create_campaign(CampaignCreate(name="B", prompt="p"))

    await assign_account_to_campaign(a.campaign_id, "acc-1")
    await assign_account_to_campaign(b.campaign_id, "acc-1")
    await assign_account_to_campaign(a.campaign_id, "acc-1")  # idempotent re-assign

    in_a = await list_campaign_accounts(a.campaign_id)
    assert [link.account_id for link in in_a.links] == ["acc-1"]
    in_b = await list_campaign_accounts(b.campaign_id)
    assert [link.campaign_id for link in in_b.links] == [b.campaign_id]


@pytest.mark.asyncio
async def test_new_assignment_has_empty_channel_subset() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")

    links = (await list_campaign_accounts(campaign.campaign_id)).links
    assert [link.channels for link in links] == [[]]  # empty subset = all channels


@pytest.mark.asyncio
async def test_set_campaign_account_channels_persists_and_clears() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@news")
    await link_channel_to_campaign(campaign.campaign_id, "@sport")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")

    await set_campaign_account_channels(campaign.campaign_id, "acc-1", ["@news", "@sport"])
    pinned = (await list_campaign_accounts(campaign.campaign_id)).links
    assert [link.channels for link in pinned] == [["@news", "@sport"]]  # sorted by channel

    await set_campaign_account_channels(campaign.campaign_id, "acc-1", [])  # clear the subset
    cleared = (await list_campaign_accounts(campaign.campaign_id)).links
    assert [link.channels for link in cleared] == [[]]


@pytest.mark.asyncio
async def test_set_campaign_account_channels_rejects_foreign_channel() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@news")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")

    # A channel that is not an active link of this campaign is rejected, and the
    # stored subset is left untouched.
    with pytest.raises(ChannelNotInCampaignError):
        await set_campaign_account_channels(campaign.campaign_id, "acc-1", ["@other"])
    links = (await list_campaign_accounts(campaign.campaign_id)).links
    assert [link.channels for link in links] == [[]]


@pytest.mark.asyncio
async def test_remove_account_from_campaign_removes_link() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")

    await remove_account_from_campaign(campaign.campaign_id, "acc-1")

    remaining = await list_campaign_accounts(campaign.campaign_id)
    assert remaining.links == []


@pytest.mark.asyncio
async def test_remove_account_from_campaign_is_scoped_to_campaign() -> None:
    # Removing acc-1 from campaign A must NOT touch its link in campaign B (the
    # m2m is per-pair; shared readiness across campaigns must stay intact).
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    a = await create_campaign(CampaignCreate(name="A", prompt="p"))
    b = await create_campaign(CampaignCreate(name="B", prompt="p"))
    await assign_account_to_campaign(a.campaign_id, "acc-1")
    await assign_account_to_campaign(b.campaign_id, "acc-1")

    await remove_account_from_campaign(a.campaign_id, "acc-1")

    assert (await list_campaign_accounts(a.campaign_id)).links == []
    in_b = await list_campaign_accounts(b.campaign_id)
    assert [link.account_id for link in in_b.links] == ["acc-1"]


@pytest.mark.asyncio
async def test_remove_account_from_campaign_is_idempotent_when_absent() -> None:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))

    # No link exists — removing is a no-op, not an error.
    await remove_account_from_campaign(campaign.campaign_id, "ghost")

    assert (await list_campaign_accounts(campaign.campaign_id)).links == []


@pytest.mark.asyncio
async def test_fetch_active_campaign_for_channel_returns_active_only() -> None:
    active = await create_campaign(CampaignCreate(name="Live", prompt="p", status="active"))
    paused = await create_campaign(CampaignCreate(name="Off", prompt="p", status="paused"))

    await link_channel_to_campaign(active.campaign_id, "@live")
    await link_channel_to_campaign(paused.campaign_id, "@paused")
    # An inactive channel link in an active campaign is not a watch target.
    await link_channel_to_campaign(active.campaign_id, "@dropped")
    await deactivate_channel(active.campaign_id, "@dropped")

    found = await fetch_active_campaign_for_channel("@live")
    assert found is not None
    assert found.campaign_id == active.campaign_id

    # Active link but the campaign itself is paused → no match.
    assert await fetch_active_campaign_for_channel("@paused") is None
    # Link deactivated → no match.
    assert await fetch_active_campaign_for_channel("@dropped") is None
    assert await fetch_active_campaign_for_channel("@never") is None


@pytest.mark.asyncio
async def test_list_active_watch_channels_only_active_links_and_campaigns() -> None:
    active = await create_campaign(CampaignCreate(name="Live", prompt="p", status="active"))
    paused = await create_campaign(CampaignCreate(name="Off", prompt="p", status="paused"))

    await link_channel_to_campaign(active.campaign_id, "@a")
    await link_channel_to_campaign(active.campaign_id, "@b")
    await link_channel_to_campaign(paused.campaign_id, "@p")
    await link_channel_to_campaign(active.campaign_id, "@gone")
    await deactivate_channel(active.campaign_id, "@gone")

    watch = await list_active_watch_channels()
    assert set(watch.channels) == {"@a", "@b"}


@pytest.mark.asyncio
async def test_update_campaign_prompt_replaces_text() -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="old prompt"))
    await update_campaign_prompt(campaign.campaign_id, "new prompt")
    got = await fetch_campaign(campaign.campaign_id)
    assert got is not None
    assert got.prompt == "new prompt"
