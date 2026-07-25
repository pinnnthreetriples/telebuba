"""Discovery-candidate repository tests (the "Найти каналы" scratch set)."""

from __future__ import annotations

import pytest

from core.db import create_campaign, delete_campaign
from core.repositories.neurocomment import (
    list_discovery_candidates,
    list_pending_discovery_candidates,
    mark_discovery_qualified,
    replace_discovery_candidates,
)
from schemas.neurocomment import CampaignCreate
from schemas.neurocomment_discovery import DiscoveryCandidateRow


def _row(channel: str, *, subscribers: int | None = None, title: str = "") -> DiscoveryCandidateRow:
    return DiscoveryCandidateRow(
        channel=channel,
        title=title,
        subscribers=subscribers,
        source="telegram_search",
    )


@pytest.mark.asyncio
async def test_replace_inserts_and_lists_ordered_by_channel() -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await replace_discovery_candidates(
        campaign.campaign_id,
        [_row("gamma"), _row("alpha", subscribers=500, title="Alpha"), _row("beta")],
    )

    rows = await list_discovery_candidates(campaign.campaign_id)
    assert [row.channel for row in rows.rows] == ["alpha", "beta", "gamma"]
    alpha = rows.rows[0]
    assert alpha.title == "Alpha"
    assert alpha.subscribers == 500
    assert alpha.source == "telegram_search"
    assert alpha.qualified_at is None
    assert alpha.qualify_error is None


@pytest.mark.asyncio
async def test_replace_supersedes_the_previous_set() -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await replace_discovery_candidates(campaign.campaign_id, [_row("old1"), _row("old2")])

    await replace_discovery_candidates(campaign.campaign_id, [_row("new1")])

    rows = await list_discovery_candidates(campaign.campaign_id)
    assert [row.channel for row in rows.rows] == ["new1"]


@pytest.mark.asyncio
async def test_replace_with_empty_list_clears_the_set() -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await replace_discovery_candidates(campaign.campaign_id, [_row("only")])

    await replace_discovery_candidates(campaign.campaign_id, [])

    rows = await list_discovery_candidates(campaign.campaign_id)
    assert rows.rows == []


@pytest.mark.asyncio
async def test_replace_is_idempotent_for_the_same_input() -> None:
    """A re-run with identical results must not trip the (campaign, channel) PK."""
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    payload = [_row("alpha"), _row("beta")]

    await replace_discovery_candidates(campaign.campaign_id, payload)
    await replace_discovery_candidates(campaign.campaign_id, payload)

    rows = await list_discovery_candidates(campaign.campaign_id)
    assert [row.channel for row in rows.rows] == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_candidate_sets_are_scoped_per_campaign() -> None:
    first = await create_campaign(CampaignCreate(name="A", prompt="p"))
    second = await create_campaign(CampaignCreate(name="B", prompt="p"))
    await replace_discovery_candidates(first.campaign_id, [_row("shared")])
    await replace_discovery_candidates(second.campaign_id, [_row("shared")])

    # Same channel in two campaigns is fine — the PK is (campaign_id, channel).
    assert len((await list_discovery_candidates(first.campaign_id)).rows) == 1
    assert len((await list_discovery_candidates(second.campaign_id)).rows) == 1

    await replace_discovery_candidates(first.campaign_id, [])
    assert len((await list_discovery_candidates(second.campaign_id)).rows) == 1


@pytest.mark.asyncio
async def test_list_pending_returns_only_unprobed_rows() -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await replace_discovery_candidates(
        campaign.campaign_id,
        [_row("done"), _row("failed"), _row("waiting")],
    )

    await mark_discovery_qualified(campaign.campaign_id, "done")
    await mark_discovery_qualified(campaign.campaign_id, "failed", error="FloodWait(30s)")

    pending = await list_pending_discovery_candidates(campaign.campaign_id)
    assert [row.channel for row in pending.rows] == ["waiting"]


@pytest.mark.asyncio
async def test_mark_qualified_records_the_attempt_with_and_without_error() -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await replace_discovery_candidates(campaign.campaign_id, [_row("ok"), _row("bad")])

    await mark_discovery_qualified(campaign.campaign_id, "ok")
    await mark_discovery_qualified(campaign.campaign_id, "bad", error="channel_not_found")

    by_channel = {
        row.channel: row for row in (await list_discovery_candidates(campaign.campaign_id)).rows
    }
    assert by_channel["ok"].qualified_at is not None
    assert by_channel["ok"].qualify_error is None
    assert by_channel["bad"].qualified_at is not None
    assert by_channel["bad"].qualify_error == "channel_not_found"


@pytest.mark.asyncio
async def test_mark_qualified_backfills_subscribers() -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await replace_discovery_candidates(campaign.campaign_id, [_row("native")])

    await mark_discovery_qualified(campaign.campaign_id, "native", subscribers=1234)

    rows = await list_discovery_candidates(campaign.campaign_id)
    assert rows.rows[0].subscribers == 1234


@pytest.mark.asyncio
async def test_mark_qualified_keeps_a_known_count_when_the_probe_learned_none() -> None:
    """A failed probe must not wipe what the catalogue already told us."""
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await replace_discovery_candidates(campaign.campaign_id, [_row("cat", subscribers=900)])

    await mark_discovery_qualified(campaign.campaign_id, "cat", error="FloodWait(60s)")

    rows = await list_discovery_candidates(campaign.campaign_id)
    assert rows.rows[0].subscribers == 900


@pytest.mark.asyncio
async def test_mark_qualified_for_an_unknown_channel_is_a_noop() -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await replace_discovery_candidates(campaign.campaign_id, [_row("known")])

    await mark_discovery_qualified(campaign.campaign_id, "ghost")

    rows = await list_discovery_candidates(campaign.campaign_id)
    assert [row.channel for row in rows.rows] == ["known"]
    assert rows.rows[0].qualified_at is None


@pytest.mark.asyncio
async def test_deleting_a_campaign_cascades_to_its_candidates() -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await replace_discovery_candidates(campaign.campaign_id, [_row("orphan")])

    await delete_campaign(campaign.campaign_id)

    assert (await list_discovery_candidates(campaign.campaign_id)).rows == []
