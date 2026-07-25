"""Neurocomment readiness and ban repository tests."""

from __future__ import annotations

import pytest

from core.db import (  # type: ignore[attr-defined]
    assign_account_to_campaign,
    clear_pair_banned,
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
async def test_clear_pair_banned_restores_ready_only_for_a_banned_row() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)
    await mark_pair_banned("acc-1", "@chan")

    await clear_pair_banned("acc-1", "@chan")

    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    assert readiness.banned is False
    assert readiness.ready is True  # can_send proof restores selectability


@pytest.mark.asyncio
async def test_clear_pair_banned_keeps_ready_off_for_a_human_skipped_pair() -> None:
    # ban → operator skip → can_send probe: the un-ban must not resurrect ready, or the
    # board would show a skipped pair as "ready" while the engine still excludes it.
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)
    await mark_pair_banned("acc-1", "@chan")
    await mark_human_skipped("acc-1", "@chan")

    await clear_pair_banned("acc-1", "@chan")

    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    assert readiness.banned is False  # ban lifted
    assert readiness.human_skipped is True  # ...but the operator skip survives
    assert readiness.ready is False  # ...so the pair stays unselectable


@pytest.mark.asyncio
async def test_clear_pair_banned_is_a_noop_when_not_banned() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=False, ready=False)

    await clear_pair_banned("acc-1", "@chan")

    # Never banned → the row is untouched (no spurious ready flip).
    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    assert readiness.ready is False


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
