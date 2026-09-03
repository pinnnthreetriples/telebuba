"""Neurocomment readiness and ban repository tests."""

from __future__ import annotations

import pytest

from core.db import (  # type: ignore[attr-defined]
    assign_account_to_campaign,
    create_account,
    create_campaign,
    delete_readiness,
    fetch_linked_group,
    fetch_readiness,
    link_channel_to_campaign,
    list_campaign_readiness,
    list_channel_readiness,
    mark_human_skipped,
    mark_pair_banned,
    upsert_linked_group,
    upsert_readiness,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate


@pytest.mark.asyncio
async def test_mark_human_skipped_clears_ready_and_sets_flag() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)

    await mark_human_skipped("acc-1", "@chan")

    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    assert readiness.ready is False
    assert readiness.human_skipped is True


@pytest.mark.asyncio
async def test_mark_pair_banned_clears_ready_and_sets_flag() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)

    await mark_pair_banned("acc-1", "@chan")

    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    assert readiness.ready is False
    assert readiness.banned is True


@pytest.mark.asyncio
async def test_upsert_readiness_preserves_banned_so_a_reonboard_cannot_revive_it() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)
    await mark_pair_banned("acc-1", "@chan")

    # A re-onboard writes readiness again — the ban must survive it.
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)

    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    assert readiness.banned is True


@pytest.mark.asyncio
async def test_a_ban_survives_a_re_onboard_and_only_deleting_the_row_clears_it() -> None:
    """A ban is permanent: nothing short of dropping the row lifts it.

    ``upsert_readiness`` is the write every onboarding pass makes, so if it touched
    ``banned`` a re-onboard would silently revive a pair the channel threw out. The live
    can_send probe behind "Проверить каналы" used to lift a ban and was removed — a ban
    is now what the operator is told it is, closed for good, and this pins that there is
    no remaining path back except deleting the pair outright.
    """
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)
    await mark_pair_banned("acc-1", "@chan")

    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)

    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    assert readiness.banned is True

    await delete_readiness("acc-1", "@chan")
    assert await fetch_readiness("acc-1", "@chan") is None


@pytest.mark.asyncio
async def test_delete_readiness_removes_the_row() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)

    await delete_readiness("acc-1", "@chan")

    assert await fetch_readiness("acc-1", "@chan") is None


@pytest.mark.asyncio
async def test_linked_group_cache_upsert_and_fetch() -> None:
    assert await fetch_linked_group("@chan") is None

    enabled = await upsert_linked_group("@chan", 4423644084, comments_enabled=True)
    assert enabled.linked_chat_id == 4423644084
    assert enabled.comments_enabled is True

    disabled = await upsert_linked_group("@silent", None, comments_enabled=False)
    assert disabled.linked_chat_id is None
    assert disabled.comments_enabled is False

    refreshed = await upsert_linked_group("@chan", 999, comments_enabled=True)
    assert refreshed.linked_chat_id == 999
    fetched = await fetch_linked_group("@chan")
    assert fetched is not None
    assert fetched.linked_chat_id == 999


@pytest.mark.asyncio
async def test_an_upsert_without_the_probe_facts_keeps_the_cached_ones() -> None:
    """Onboarding refreshes comments only; nulling the two facts made discovery re-probe forever."""
    await upsert_linked_group("@chan", 1, comments_enabled=True, about="News", join_request=False)

    refreshed = await upsert_linked_group("@chan", 1, comments_enabled=False)

    assert refreshed.comments_enabled is False
    assert (refreshed.about, refreshed.join_request) == ("News", False)


@pytest.mark.asyncio
async def test_readiness_upsert_and_fetch() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    assert await fetch_readiness("acc-1", "@chan") is None

    first = await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=False, ready=False)
    assert first.joined is True
    assert first.captcha_passed is False
    assert first.ready is False

    second = await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)
    assert second.ready is True
    fetched = await fetch_readiness("acc-1", "@chan")
    assert fetched is not None
    assert fetched.ready is True


@pytest.mark.asyncio
async def test_list_channel_readiness_narrows_the_campaign_read_to_one_channel() -> None:
    # The engine used to load every (account, channel) pair of the campaign per post and
    # filter the channel in Python; this reader does both filters in SQL. The
    # campaign-accounts subquery still guards the shared-account leak, so an account_id
    # from another campaign is dropped even when a caller passes it in.
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    other = await create_campaign(CampaignCreate(name="B", prompt="p"))
    for channel in ("@one", "@two"):
        await link_channel_to_campaign(campaign.campaign_id, channel)
    for acc in ("acc-1", "acc-2", "acc-outside"):
        await create_account(AccountCreate(account_id=acc, label=acc, session_name=acc))
        await upsert_readiness(acc, "@one", joined=True, captcha_passed=True, ready=True)
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await assign_account_to_campaign(campaign.campaign_id, "acc-2")
    await assign_account_to_campaign(other.campaign_id, "acc-outside")
    await upsert_readiness("acc-1", "@two", joined=True, captcha_passed=True, ready=True)

    rows = await list_channel_readiness(campaign.campaign_id, "@one", ["acc-1", "acc-2"])
    assert {(r.account_id, r.channel) for r in rows.readiness} == {
        ("acc-1", "@one"),
        ("acc-2", "@one"),
    }
    # The campaign-wide read is what it narrows: same accounts, but both channels.
    wide = await list_campaign_readiness(campaign.campaign_id)
    assert {(r.account_id, r.channel) for r in wide.readiness} == {
        ("acc-1", "@one"),
        ("acc-2", "@one"),
        ("acc-1", "@two"),
    }

    # Candidate-scoped: a campaign account left out of the list is not returned.
    only_one = await list_channel_readiness(campaign.campaign_id, "@one", ["acc-1"])
    assert [r.account_id for r in only_one.readiness] == ["acc-1"]
    # Another campaign's account can't leak in, and an empty list skips the query.
    leak = await list_channel_readiness(campaign.campaign_id, "@one", ["acc-outside"])
    assert leak.readiness == []
    assert (await list_channel_readiness(campaign.campaign_id, "@one", [])).readiness == []
