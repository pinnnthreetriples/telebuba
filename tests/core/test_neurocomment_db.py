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
    count_by_outcome,
    count_channel_comments_per_account_since,
    count_comments_per_account_since,
    create_account,
    create_campaign,
    deactivate_channel,
    delete_readiness,
    fetch_active_campaign_for_channel,
    fetch_campaign,
    fetch_comment,
    fetch_linked_group,
    fetch_readiness,
    insert_challenge,
    link_channel_to_campaign,
    list_active_watch_channels,
    list_campaign_accounts,
    list_campaign_channels,
    list_campaigns,
    list_failed_for_channel,
    list_posted_comments_for_channel_since,
    lookup_cached_decision,
    mark_comment_failed,
    mark_comment_posted,
    mark_human_skipped,
    reclaim_stale_claims,
    remove_account_from_campaign,
    resolve_pending_outcome,
    update_campaign_prompt,
    update_solver_enabled,
    upsert_linked_group,
    upsert_readiness,
)
from core.repositories.neurocomment import (
    ChannelNotInCampaignError,
    set_campaign_account_channel,
)
from schemas.accounts import AccountCreate
from schemas.challenge import ChallengeDecision, ChallengeInsert
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
    "neurocomment_challenges",
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


def test_neurocomment_comment_indexes_created() -> None:
    engine = _get_engine()
    index_names = {ix["name"] for ix in inspect(engine).get_indexes("neurocomment_comments")}
    assert {
        "ix_nc_comments_account_status_created",
        "ix_nc_comments_channel_account_status_created",
        "ix_nc_comments_campaign_channel_status_created",
    } <= index_names
    with engine.connect() as connection:
        versions = {
            int(row[0]) for row in connection.exec_driver_sql("SELECT version FROM schema_version")
        }
    assert 13 in versions


def test_challenges_table_indexes_and_column_created() -> None:
    """Migration #14 lands the audit table, both indexes, and solver_enabled (v14)."""
    engine = _get_engine()
    inspector = inspect(engine)
    assert "neurocomment_challenges" in inspector.get_table_names()
    index_names = {ix["name"] for ix in inspector.get_indexes("neurocomment_challenges")}
    assert {
        "ix_nc_challenges_hash_outcome",
        "ix_nc_challenges_account_channel_decided",
    } <= index_names
    with engine.connect() as connection:
        campaign_columns = {
            row["name"]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(neurocomment_campaigns)",
            ).mappings()
        }
        versions = {
            int(row[0]) for row in connection.exec_driver_sql("SELECT version FROM schema_version")
        }
    assert "solver_enabled" in campaign_columns
    assert 14 in versions


@pytest.mark.asyncio
async def test_migration_14_idempotent_on_database_with_neurocomment_data() -> None:
    """Migration #14's body re-runs cleanly over a populated DB (guards no-op)."""
    from core.migrations import apply_migrations  # noqa: PLC0415

    await create_campaign(CampaignCreate(name="C", prompt="p"))
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=False, ready=False)

    engine = _get_engine()
    # Drop the v14 stamp so the body actually re-executes against the populated DB
    # (a plain re-run would skip it as already-applied — see test_migrations.py).
    with engine.begin() as connection:
        connection.exec_driver_sql("DELETE FROM schema_version WHERE version = 14")
    apply_migrations(engine)  # body re-runs; guards must make it a no-op, not raise

    with engine.connect() as connection:
        campaign_columns = {
            row["name"]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(neurocomment_campaigns)",
            ).mappings()
        }
        campaign_count = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM neurocomment_campaigns",
        ).scalar_one()
        versions = {
            int(row[0]) for row in connection.exec_driver_sql("SELECT version FROM schema_version")
        }
    assert "solver_enabled" in campaign_columns
    assert int(campaign_count) == 1
    assert 14 in versions


@pytest.mark.asyncio
async def test_insert_challenge_and_list_failed_for_channel() -> None:
    await insert_challenge(
        ChallengeInsert(
            challenge_hash="h1",
            account_id="acc-1",
            channel="@chan",
            raw_text="нажми, чтобы остаться",
            button_labels=["Я не бот", "Я бот"],
            outcome="give_up",
        ),
    )

    result = await list_failed_for_channel("@chan", limit=10)

    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.account_id == "acc-1"
    assert row.channel == "@chan"
    assert row.raw_text == "нажми, чтобы остаться"
    assert row.button_labels == ["Я не бот", "Я бот"]
    assert row.outcome == "give_up"


@pytest.mark.asyncio
async def test_list_failed_for_channel_excludes_solved() -> None:
    await insert_challenge(
        ChallengeInsert(
            challenge_hash="h1",
            account_id="acc-1",
            channel="@chan",
            raw_text="solved one",
            button_labels=["ok"],
            outcome="solved",
        ),
    )

    result = await list_failed_for_channel("@chan", limit=10)

    assert result.rows == []


@pytest.mark.asyncio
async def test_list_failed_for_channel_is_newest_first_and_limited() -> None:
    for i in range(3):
        await insert_challenge(
            ChallengeInsert(
                challenge_hash=f"h{i}",
                account_id="acc-1",
                channel="@chan",
                raw_text=f"challenge {i}",
                button_labels=["x"],
                outcome="give_up",
            ),
        )

    result = await list_failed_for_channel("@chan", limit=2)

    # Newest-first (id desc tiebreaker), capped at the limit.
    assert [r.raw_text for r in result.rows] == ["challenge 2", "challenge 1"]


def _solved_insert(
    challenge_hash: str, account_id: str, decision: ChallengeDecision
) -> ChallengeInsert:
    return ChallengeInsert(
        challenge_hash=challenge_hash,
        account_id=account_id,
        channel="@chan",
        raw_text="prove human",
        button_labels=["yes"],
        outcome="solved",
        decision_json=decision.model_dump_json(),
    )


@pytest.mark.asyncio
async def test_lookup_cached_decision_returns_solved_decision() -> None:
    decision = ChallengeDecision(
        action="click_button", button_index=2, confidence=0.8, reasoning="r"
    )
    await insert_challenge(_solved_insert("hash-1", "acc-1", decision))

    cached = await lookup_cached_decision("hash-1")

    assert cached is not None
    assert cached.action == "click_button"
    assert cached.button_index == 2


@pytest.mark.asyncio
async def test_lookup_cached_decision_ignores_non_solved() -> None:
    await insert_challenge(
        ChallengeInsert(
            challenge_hash="hash-2",
            account_id="acc-1",
            channel="@chan",
            raw_text="x",
            button_labels=["y"],
            outcome="give_up",
        ),
    )

    assert await lookup_cached_decision("hash-2") is None


@pytest.mark.asyncio
async def test_resolve_pending_outcome_marks_latest_pending() -> None:
    await insert_challenge(
        ChallengeInsert(
            challenge_hash="h",
            account_id="acc-1",
            channel="@chan",
            raw_text="x",
            button_labels=["y"],
            outcome="pending",
            decision_json=ChallengeDecision(
                action="click_button", button_index=0, confidence=0.9, reasoning="r"
            ).model_dump_json(),
        ),
    )

    await resolve_pending_outcome("acc-1", "@chan", "solved")

    engine = _get_engine()
    with engine.connect() as connection:
        row = (
            connection.exec_driver_sql(
                "SELECT outcome, outcome_at FROM neurocomment_challenges WHERE account_id='acc-1'",
            )
            .mappings()
            .first()
        )
    assert row is not None
    assert row["outcome"] == "solved"
    assert row["outcome_at"] is not None


@pytest.mark.asyncio
async def test_resolve_pending_outcome_is_noop_without_pending() -> None:
    # No pending row for the pair → must not raise.
    await resolve_pending_outcome("ghost", "@chan", "failed")


def test_migration_15_adds_human_skipped_column() -> None:
    engine = _get_engine()
    with engine.connect() as connection:
        columns = {
            row["name"]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(neurocomment_readiness)",
            ).mappings()
        }
        versions = {
            int(row[0]) for row in connection.exec_driver_sql("SELECT version FROM schema_version")
        }
    assert "human_skipped" in columns
    assert 15 in versions


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
async def test_mark_human_skipped_clears_ready_and_sets_flag() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)

    await mark_human_skipped("acc-1", "@chan")

    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    assert readiness.ready is False
    assert readiness.human_skipped is True


@pytest.mark.asyncio
async def test_delete_readiness_removes_the_row() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)

    await delete_readiness("acc-1", "@chan")

    assert await fetch_readiness("acc-1", "@chan") is None


@pytest.mark.asyncio
async def test_count_by_outcome_groups_and_windows() -> None:
    for outcome in ("solved", "solved", "failed", "give_up"):
        await insert_challenge(
            ChallengeInsert(
                challenge_hash="h",
                account_id="acc-1",
                channel="@chan",
                raw_text="x",
                button_labels=["y"],
                outcome=outcome,
            ),
        )

    counts = await count_by_outcome(["@chan"], since="")
    assert (counts.solved, counts.failed, counts.give_up, counts.pending) == (2, 1, 1, 0)
    # A future lower bound excludes everything.
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    empty = await count_by_outcome(["@chan"], since=future)
    assert (empty.solved, empty.failed, empty.give_up) == (0, 0, 0)
    # A channel outside the set is not counted.
    assert (await count_by_outcome(["@other"], since="")).solved == 0


@pytest.mark.asyncio
async def test_list_failed_for_channel_surfaces_reasoning() -> None:
    decision = ChallengeDecision(action="give_up", confidence=0.4, reasoning="image captcha")
    await insert_challenge(
        ChallengeInsert(
            challenge_hash="h",
            account_id="acc-1",
            channel="@chan",
            raw_text="x",
            button_labels=["y"],
            outcome="give_up",
            decision_json=decision.model_dump_json(),
        ),
    )

    rows = (await list_failed_for_channel("@chan", limit=10)).rows
    assert rows[0].reasoning == "image captcha"


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
async def test_new_assignment_has_null_channel_pin() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")

    links = (await list_campaign_accounts(campaign.campaign_id)).links
    assert [link.channel for link in links] == [None]  # NULL = all channels


@pytest.mark.asyncio
async def test_set_campaign_account_channel_persists_and_clears() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@news")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")

    await set_campaign_account_channel(campaign.campaign_id, "acc-1", "@news")
    pinned = (await list_campaign_accounts(campaign.campaign_id)).links
    assert [link.channel for link in pinned] == ["@news"]

    await set_campaign_account_channel(campaign.campaign_id, "acc-1", None)  # clear the pin
    cleared = (await list_campaign_accounts(campaign.campaign_id)).links
    assert [link.channel for link in cleared] == [None]


@pytest.mark.asyncio
async def test_set_campaign_account_channel_rejects_foreign_channel() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@news")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")

    # Pinning to a channel that is not an active link of this campaign is rejected,
    # and the stored pin is left untouched.
    with pytest.raises(ChannelNotInCampaignError):
        await set_campaign_account_channel(campaign.campaign_id, "acc-1", "@other")
    links = (await list_campaign_accounts(campaign.campaign_id)).links
    assert [link.channel for link in links] == [None]


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


async def _backdate_created_at(post_id: int, when: datetime) -> None:
    """Test-only: rewrite one comment's created_at (mirrors the board test's idiom)."""
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_comments SET created_at = ? WHERE post_id = ?",
            (when.isoformat(), post_id),
        )


@pytest.mark.asyncio
async def test_reclaim_stale_claims_marks_old_claimed_as_failed() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    assert await claim_comment("@chan", 1, campaign.campaign_id, "acc-1") is True
    # A claim stuck since before the cutoff (a crash mid-post left it 'claimed').
    await _backdate_created_at(1, datetime.now(UTC) - timedelta(hours=1))

    reclaimed = await reclaim_stale_claims(datetime.now(UTC).isoformat())

    assert reclaimed == 1
    row = await fetch_comment("@chan", 1)
    assert row is not None
    assert row.status == "failed"


@pytest.mark.asyncio
async def test_reclaim_stale_claims_leaves_fresh_claim_untouched() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    assert await claim_comment("@chan", 1, campaign.campaign_id, "acc-1") is True

    # Cutoff an hour in the past — a just-created claim is newer, so it is left alone.
    reclaimed = await reclaim_stale_claims((datetime.now(UTC) - timedelta(hours=1)).isoformat())

    assert reclaimed == 0
    row = await fetch_comment("@chan", 1)
    assert row is not None
    assert row.status == "claimed"


@pytest.mark.asyncio
async def test_reclaim_stale_claims_leaves_posted_untouched() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    assert await claim_comment("@chan", 1, campaign.campaign_id, "acc-1") is True
    await mark_comment_posted("@chan", 1, comment_text="nice", comment_msg_id=5)
    # Old, but terminal (posted) — the reclaim only touches rows still 'claimed'.
    await _backdate_created_at(1, datetime.now(UTC) - timedelta(hours=1))

    reclaimed = await reclaim_stale_claims(datetime.now(UTC).isoformat())

    assert reclaimed == 0
    row = await fetch_comment("@chan", 1)
    assert row is not None
    assert row.status == "posted"


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
async def test_bulk_per_account_counts_match_per_account_readers() -> None:
    # The bulk grouped readers (Tier 2 selection) must agree with the trusted
    # per-account readers they replace — account-wide and per-channel.
    for acc in ("acc-1", "acc-2"):
        await create_account(AccountCreate(account_id=acc, label=acc, session_name=acc))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))

    await _post_one("@one", 1, campaign.campaign_id, "acc-1")
    await _post_one("@one", 2, campaign.campaign_id, "acc-1")
    await _post_one("@two", 3, campaign.campaign_id, "acc-1")
    await _post_one("@one", 4, campaign.campaign_id, "acc-2")
    # An in-flight claim (claimed, not yet posted) consumes quota too.
    assert await claim_comment("@one", 5, campaign.campaign_id, "acc-2") is True

    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    grouped = await count_comments_per_account_since(past)
    per_account = {c.account_id: c.count for c in grouped.counts}
    assert per_account == {"acc-1": 3, "acc-2": 2}
    for acc in ("acc-1", "acc-2", "ghost"):
        assert per_account.get(acc, 0) == await count_account_comments_since(acc, past)

    channel_grouped = await count_channel_comments_per_account_since("@one", past)
    per_channel = {c.account_id: c.count for c in channel_grouped.counts}
    assert per_channel == {"acc-1": 2, "acc-2": 2}
    for acc in ("acc-1", "acc-2", "ghost"):
        expected = await count_account_channel_comments_since(acc, "@one", past)
        assert per_channel.get(acc, 0) == expected


@pytest.mark.asyncio
async def test_list_posted_comments_for_channel_is_scoped_to_channel_and_posted() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p"))
    await _post_one("@one", 1, campaign.campaign_id, "acc-1")
    await _post_one("@one", 2, campaign.campaign_id, "acc-1")
    await _post_one("@two", 3, campaign.campaign_id, "acc-1")
    # A claimed-but-not-posted comment is excluded (status != posted).
    assert await claim_comment("@one", 4, campaign.campaign_id, "acc-1") is True

    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    one = await list_posted_comments_for_channel_since(campaign.campaign_id, "@one", past)
    assert {c.post_id for c in one.comments} == {1, 2}
    two = await list_posted_comments_for_channel_since(campaign.campaign_id, "@two", past)
    assert {c.post_id for c in two.comments} == {3}
    none = await list_posted_comments_for_channel_since(campaign.campaign_id, "@none", past)
    assert none.comments == []


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
