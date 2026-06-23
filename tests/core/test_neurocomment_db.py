"""Tests for the neurocomment data layer — config, migrations, repository."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from core.config import settings
from core.db import (  # type: ignore[attr-defined]
    ChannelAlreadyAssignedError,
    _get_engine,
    assign_account_to_campaign,
    claim_comment,
    configure_database,
    count_account_channel_comments_since,
    count_account_comments_since,
    create_account,
    create_campaign,
    deactivate_channel,
    fetch_active_campaign_for_channel,
    fetch_campaign,
    fetch_comment,
    fetch_linked_group,
    fetch_readiness,
    link_channel_to_campaign,
    list_active_watch_channels,
    list_campaign_accounts,
    list_campaign_channels,
    list_campaigns,
    mark_comment_failed,
    mark_comment_posted,
    remove_account_from_campaign,
    upsert_linked_group,
    upsert_readiness,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate

if TYPE_CHECKING:
    from pathlib import Path

_NEUROCOMMENT_TABLES = {
    "neurocomment_campaigns",
    "neurocomment_campaign_channels",
    "neurocomment_campaign_accounts",
    "neurocomment_linked_groups",
    "neurocomment_readiness",
    "neurocomment_comments",
}


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


def test_neurocomment_settings_have_issue_defaults() -> None:
    nc = settings.neurocomment
    assert (nc.reply_delay_min_seconds, nc.reply_delay_max_seconds) == (3.0, 10.0)
    assert (nc.join_delay_min_seconds, nc.join_delay_max_seconds) == (30.0, 60.0)
    assert nc.max_comments_per_hour == 10
    assert nc.comment_max_words == 30
    assert nc.max_comments_per_channel_per_day == 3
    assert nc.max_retries == 2


def test_neurocomment_tables_created_and_migration_stamped() -> None:
    engine = _get_engine()
    tables = set(inspect(engine).get_table_names())
    assert tables >= _NEUROCOMMENT_TABLES
    with engine.connect() as connection:
        versions = {
            int(row[0]) for row in connection.exec_driver_sql("SELECT version FROM schema_version")
        }
    assert 11 in versions


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
async def test_comment_claim_is_idempotent_and_records_outcome() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))

    assert await claim_comment("@chan", 100, campaign.campaign_id, "acc-1") is True
    # the same post can only be claimed once across the fleet
    assert await claim_comment("@chan", 100, campaign.campaign_id, "acc-1") is False

    pending = await fetch_comment("@chan", 100)
    assert pending is not None
    assert pending.status == "claimed"

    posted = await mark_comment_posted("@chan", 100, comment_text="nice", comment_msg_id=555)
    assert posted is not None
    assert posted.status == "posted"
    assert posted.comment_text == "nice"
    assert posted.comment_msg_id == 555

    assert await claim_comment("@chan", 101, campaign.campaign_id, "acc-1") is True
    failed = await mark_comment_failed("@chan", 101)
    assert failed is not None
    assert failed.status == "failed"

    # marking a post nobody claimed yields None
    assert await mark_comment_posted("@chan", 999, comment_text="x", comment_msg_id=1) is None


@pytest.mark.asyncio
async def test_mark_comment_does_not_override_a_terminal_status() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    assert await claim_comment("@chan", 100, campaign.campaign_id, "acc-1") is True
    await mark_comment_posted("@chan", 100, comment_text="nice", comment_msg_id=555)

    # A late failure must not flip an already-posted claim back to failed.
    result = await mark_comment_failed("@chan", 100)
    assert result is not None
    assert result.status == "posted"
    assert result.comment_text == "nice"


# --------------------------------------------------------------------------- #
# Engine helpers (issue #118): throughput windows + listener watch set.
# --------------------------------------------------------------------------- #


async def _post_one(channel: str, post_id: int, campaign_id: str, account_id: str) -> None:
    assert await claim_comment(channel, post_id, campaign_id, account_id) is True
    await mark_comment_posted(channel, post_id, comment_text="hi", comment_msg_id=post_id)


@pytest.mark.asyncio
async def test_count_account_comments_since_counts_claimed_and_posted_in_window() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))

    await _post_one("@chan", 1, campaign.campaign_id, "acc-1")
    await _post_one("@chan", 2, campaign.campaign_id, "acc-1")
    # An in-flight claim counts too: it must consume quota immediately so a burst
    # arriving inside the reply-delay window can't stack past the cap.
    assert await claim_comment("@chan", 3, campaign.campaign_id, "acc-1") is True

    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert await count_account_comments_since("acc-1", past) == 3
    assert await count_account_comments_since("acc-1", future) == 0
    assert await count_account_comments_since("other", past) == 0


@pytest.mark.asyncio
async def test_count_account_channel_comments_since_is_channel_scoped() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))

    await _post_one("@one", 1, campaign.campaign_id, "acc-1")
    await _post_one("@one", 2, campaign.campaign_id, "acc-1")
    await _post_one("@two", 3, campaign.campaign_id, "acc-1")

    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    assert await count_account_channel_comments_since("acc-1", "@one", past) == 2
    assert await count_account_channel_comments_since("acc-1", "@two", past) == 1
    assert await count_account_channel_comments_since("acc-1", "@none", past) == 0


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
