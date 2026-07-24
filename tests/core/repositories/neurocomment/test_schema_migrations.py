"""Neurocomment configuration, schema, and migration tests."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect

from core.config import settings
from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    create_account,
    create_campaign,
    upsert_readiness,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate

_NEUROCOMMENT_TABLES = {
    "neurocomment_campaigns",
    "neurocomment_campaign_channels",
    "neurocomment_campaign_accounts",
    "neurocomment_linked_groups",
    "neurocomment_readiness",
    "neurocomment_comments",
    "neurocomment_challenges",
}


def test_neurocomment_settings_have_issue_defaults() -> None:
    nc = settings.neurocomment
    assert (nc.reply_delay_min_seconds, nc.reply_delay_max_seconds) == (3.0, 10.0)
    assert (nc.join_delay_min_seconds, nc.join_delay_max_seconds) == (30.0, 120.0)
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


def test_migration_30_adds_banned_column() -> None:
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
    assert "banned" in columns
    assert 30 in versions
