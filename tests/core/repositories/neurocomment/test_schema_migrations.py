"""Neurocomment configuration, schema, and migration tests.

The migration #39 cases at the bottom build their own legacy SQLite databases via
the ``legacy_engine`` factory instead of using the configured engine: the fold index
has to be exercised against #11's case-SENSITIVE one to prove the upgrade path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError, OperationalError

from core.config import settings
from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    create_account,
    create_campaign,
    list_joined_watch_channels,
    record_join,
    upsert_readiness,
)
from core.migration_steps_budget_reset import _reset_overshot_retry_budgets
from core.migration_steps_captcha_giveup import _add_readiness_captcha_giveup
from core.migration_steps_join_lost import _add_neurocomment_join_log_lost_at
from core.migration_steps_neurocomment import _add_neurocomment_channel_case_fold_index
from core.migration_steps_unconfirmed_ban import _add_readiness_unconfirmed_ban
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine

    from tests.core.conftest import _EngineFactory

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
    assert nc.max_joins_per_account_per_day == 20
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


def test_migration_35_adds_challenges_channel_index() -> None:
    """Migration #35 lands the channel-leading composite index on the challenges table."""
    engine = _get_engine()
    index_names = {ix["name"] for ix in inspect(engine).get_indexes("neurocomment_challenges")}
    assert "ix_nc_challenges_channel_outcome_decided" in index_names
    with engine.connect() as connection:
        versions = {
            int(row[0]) for row in connection.exec_driver_sql("SELECT version FROM schema_version")
        }
    assert 35 in versions


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


@pytest.mark.asyncio
async def test_migration_41_adds_join_request_columns() -> None:
    engine = _get_engine()
    with engine.connect() as connection:
        columns = {
            row["name"]: row
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(neurocomment_readiness)",
            ).mappings()
        }
        versions = {
            int(row[0]) for row in connection.exec_driver_sql("SELECT version FROM schema_version")
        }
    # Nullable stamp, NOT NULL counter — an existing row reads as "no request
    # outstanding", which is what it was.
    assert columns["join_requested_at"]["notnull"] == 0
    assert columns["join_request_attempts"]["notnull"] == 1
    assert 41 in versions

    await create_account(AccountCreate(account_id="acc-1"))
    row = await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)
    assert (row.join_requested_at, row.join_request_attempts) == (None, 0)


def test_migration_35_adds_join_log_table_and_index() -> None:
    engine = _get_engine()
    inspector = inspect(engine)
    assert "neurocomment_join_log" in inspector.get_table_names()
    index_names = {ix["name"] for ix in inspector.get_indexes("neurocomment_join_log")}
    assert "ix_nc_join_log_account_joined" in index_names
    with engine.connect() as connection:
        versions = {
            int(row[0]) for row in connection.exec_driver_sql("SELECT version FROM schema_version")
        }
    assert 35 in versions


def _legacy_campaign_channels(legacy_engine: _EngineFactory, name: str) -> Engine:
    """A legacy DB with #11's case-SENSITIVE index, so case-duplicates can be planted."""
    engine = legacy_engine(name)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE neurocomment_campaign_channels ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id VARCHAR NOT NULL, "
            "channel VARCHAR NOT NULL, active INTEGER NOT NULL, created_at VARCHAR NOT NULL)",
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX ix_neurocomment_channel_one_active_campaign "
            "ON neurocomment_campaign_channels(channel) WHERE active = 1",
        )
        connection.exec_driver_sql(
            "CREATE TABLE neurocomment_campaign_account_channels ("
            "campaign_id VARCHAR NOT NULL, account_id VARCHAR NOT NULL, "
            "channel VARCHAR NOT NULL, created_at VARCHAR NOT NULL, "
            "PRIMARY KEY (campaign_id, account_id, channel))",
        )
    return engine


def test_channel_fold_index_rejects_case_duplicates_and_is_idempotent(
    legacy_engine: _EngineFactory,
) -> None:
    """#39 recreates the one-active-campaign index over the ``dedup_key`` fold."""
    engine = _legacy_campaign_channels(legacy_engine, "fold.db")
    with engine.begin() as connection:
        _add_neurocomment_channel_case_fold_index(connection)
        _add_neurocomment_channel_case_fold_index(connection)  # idempotent — must not raise.
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_campaign_channels "
            "(campaign_id, channel, active, created_at) VALUES ('a', 'telegram', 1, 'now')",
        )
        # +HASH invite keys stay case-sensitive: both must be insertable.
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_campaign_channels "
            "(campaign_id, channel, active, created_at) VALUES ('a', '+AbCdEfGh', 1, 'now'), "
            "('b', '+abcdefgh', 1, 'now')",
        )
    # Letter case AND the decorative '@' fold away: every spelling below names the
    # same channel, and both resolve to one peer id the listener can only map once.
    for spelling in ("Telegram", "@telegram", "@TELEGRAM"):
        with engine.begin() as connection, pytest.raises(IntegrityError):
            connection.exec_driver_sql(
                "INSERT INTO neurocomment_campaign_channels "
                "(campaign_id, channel, active, created_at) VALUES ('b', ?, 1, 'now')",
                (spelling,),
            )


def test_channel_fold_index_deactivates_pre_existing_case_duplicates(
    legacy_engine: _EngineFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A DB already holding both spellings active: the LATER link is demoted, not deleted."""
    engine = _legacy_campaign_channels(legacy_engine, "dupes.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_campaign_channels "
            "(campaign_id, channel, active, created_at) VALUES "
            "('a', 'telegram', 1, 'now'), ('b', 'Telegram', 1, 'now'), ('c', 'other', 1, 'now')",
        )
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_campaign_account_channels "
            "(campaign_id, account_id, channel, created_at) VALUES "
            "('b', 'acc-1', 'Telegram', 'now'), ('c', 'acc-1', 'other', 'now')",
        )
        with caplog.at_level("WARNING"):
            _add_neurocomment_channel_case_fold_index(connection)

    with engine.connect() as connection:
        links = connection.exec_driver_sql(
            "SELECT campaign_id, channel, active FROM neurocomment_campaign_channels ORDER BY id",
        ).all()
        subsets = connection.exec_driver_sql(
            "SELECT campaign_id, channel FROM neurocomment_campaign_account_channels",
        ).all()

    # The row is kept (re-linkable by the operator), only its active flag flips.
    assert links == [("a", "telegram", 1), ("b", "Telegram", 0), ("c", "other", 1)]
    # The demoted link's per-account subset entry is dropped, as deactivate_channel does.
    assert subsets == [("c", "other")]
    # An account left with NO pins serves every channel of its campaign, so the
    # warning has to name it — the link alone does not tell the operator what to re-pin.
    assert "acc-1" in caplog.text


def test_channel_fold_sweep_keeps_a_pair_the_index_would_accept(
    legacy_engine: _EngineFactory,
) -> None:
    """The sweep must fold no harder than the index it protects.

    SQLite's ``lower()`` is ASCII-only, so ``КАНАЛ`` and ``канал`` are two distinct keys
    to the index — a sweep folding in Python instead would deactivate the second and
    DELETE its account pins, unlinking a channel the operator is running a campaign on.
    """
    engine = _legacy_campaign_channels(legacy_engine, "cyrillic.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_campaign_channels "
            "(campaign_id, channel, active, created_at) VALUES "
            "('a', 'КАНАЛ', 1, 'now'), ('b', 'канал', 1, 'now')",
        )
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_campaign_account_channels "
            "(campaign_id, account_id, channel, created_at) VALUES ('b', 'acc-1', 'канал', 'now')",
        )
        _add_neurocomment_channel_case_fold_index(connection)

    with engine.connect() as connection:
        links = connection.exec_driver_sql(
            "SELECT channel, active FROM neurocomment_campaign_channels ORDER BY id",
        ).all()
        pins = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM neurocomment_campaign_account_channels",
        ).scalar_one()
    assert links == [("КАНАЛ", 1), ("канал", 1)]
    assert int(pins) == 1


def test_channel_fold_index_is_never_absent_when_the_create_fails(
    legacy_engine: _EngineFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed CREATE must leave #11's index in place, not an unconstrained table.

    pysqlite emits no BEGIN ahead of DDL, so with nothing to sweep a leading DROP
    commits in autocommit and outlives both the failing CREATE and the registry's
    abort — after which two rows both named ``telegram`` become legal.
    """
    engine = _legacy_campaign_channels(legacy_engine, "failed-create.db")
    monkeypatch.setattr(
        "core.migration_steps_neurocomment._FOLD_INDEX",
        "not a valid index name",
    )
    with engine.begin() as connection, pytest.raises(OperationalError):
        _add_neurocomment_channel_case_fold_index(connection)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_campaign_channels "
            "(campaign_id, channel, active, created_at) VALUES ('a', 'telegram', 1, 'now')",
        )
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_campaign_channels "
            "(campaign_id, channel, active, created_at) VALUES ('b', 'telegram', 1, 'now')",
        )


def test_channel_fold_index_skips_a_db_without_the_link_table(
    legacy_engine: _EngineFactory,
) -> None:
    """The #39 body is a no-op on a hand-built DB that has no campaign-channel table."""
    engine = legacy_engine("empty-fold.db")
    with engine.begin() as connection:
        _add_neurocomment_channel_case_fold_index(connection)  # no table → returns, no raise.


@pytest.mark.asyncio
async def test_migration_45_adds_join_log_lost_at() -> None:
    """The stamp that replaced deleting a disproven join, so the join cap keeps counting."""
    engine = _get_engine()
    with engine.connect() as connection:
        columns = {
            row["name"]: row
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(neurocomment_join_log)",
            ).mappings()
        }
        versions = {
            int(row[0]) for row in connection.exec_driver_sql("SELECT version FROM schema_version")
        }
    # Nullable: an existing row means "this join still stands", which is what it meant.
    assert columns["lost_at"]["notnull"] == 0
    assert 45 in versions

    await record_join("acc-1", watch_channel="@chan")
    assert await list_joined_watch_channels("acc-1") == {"@chan"}


def test_migration_45_alters_a_join_log_that_predates_the_column(
    legacy_engine: _EngineFactory,
) -> None:
    """The only path that ever RUNS on the operator's database, exercised against a real one.

    The test above cannot reach it: ``core.db`` runs ``create_all`` before
    ``apply_migrations``, so on a test DB ``lost_at`` is already there and the body's PRAGMA
    guard returns before the ALTER. Built here the way #39's cases are — a hand-made table
    at the OLD shape, so the ALTER is what has to put the column on it.
    """
    engine = legacy_engine("join-log-pre-45.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE neurocomment_join_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, account_id VARCHAR NOT NULL, "
            "joined_at VARCHAR NOT NULL, watch_channel VARCHAR)",
        )
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_join_log (account_id, joined_at, watch_channel) "
            "VALUES ('acc-1', 'then', '@chan'), ('acc-1', 'then', NULL)",
        )
        assert "lost_at" not in _join_log_columns(connection)

        _add_neurocomment_join_log_lost_at(connection)
        assert "lost_at" in _join_log_columns(connection)
        _add_neurocomment_join_log_lost_at(connection)  # idempotent — must not raise.

    with engine.connect() as connection:
        stamps = connection.exec_driver_sql(
            "SELECT lost_at FROM neurocomment_join_log ORDER BY id",
        ).all()
    # Every pre-existing row reads NULL = "this join still stands", which is what a row
    # meant before the column existed — the watch-channel one and the group one alike.
    assert stamps == [(None,), (None,)]


def _join_log_columns(connection: Connection) -> set[str]:
    return {
        str(row["name"])
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(neurocomment_join_log)",
        ).mappings()
    }


def _readiness_columns(connection: Connection) -> dict[str, dict[str, object]]:
    return {
        str(row["name"]): dict(row)
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(neurocomment_readiness)",
        ).mappings()
    }


def test_migration_47_adds_unconfirmed_ban_columns() -> None:
    """The budget that ends a pair re-refused by the same channel over and over."""
    engine = _get_engine()
    with engine.connect() as connection:
        columns = _readiness_columns(connection)
        versions = {
            int(row[0]) for row in connection.exec_driver_sql("SELECT version FROM schema_version")
        }
    # NOT NULL counter, nullable stamp — the #41 and #43 shape. An existing row has to read
    # as "nothing collected here yet": nobody may be banned on evidence gathered before the
    # column existed.
    assert columns["unconfirmed_bans"]["notnull"] == 1
    # ``create_all`` quotes the server default and the ALTER does not, so compare the value.
    assert str(columns["unconfirmed_bans"]["dflt_value"]).strip("'") == "0"
    assert columns["unconfirmed_ban_at"]["notnull"] == 0
    assert 47 in versions


def _legacy_readiness(legacy_engine: _EngineFactory, name: str, columns: str = "") -> Engine:
    """A readiness table at the pre-#47 shape, so an ALTER is what has to add the columns."""
    engine = legacy_engine(name)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE neurocomment_readiness ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, account_id VARCHAR NOT NULL, "
            f"channel VARCHAR NOT NULL, checked_at VARCHAR NOT NULL{columns})",
        )
    return engine


def test_migration_47_alters_a_readiness_table_that_predates_the_columns(
    legacy_engine: _EngineFactory,
) -> None:
    """The only path that ever RUNS on the operator's database, exercised against a real one.

    The test above cannot reach it: ``core.db`` runs ``create_all`` before
    ``apply_migrations``, so on a test DB both columns are already there and the body's
    PRAGMA guard returns before either ALTER. Built here the way #45's case is.
    """
    engine = _legacy_readiness(legacy_engine, "readiness-pre-47.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_readiness (account_id, channel, checked_at) "
            "VALUES ('acc-1', '@chan', 'then'), ('acc-2', '@chan', 'then')",
        )
        assert "unconfirmed_bans" not in _readiness_columns(connection)

        _add_readiness_unconfirmed_ban(connection)
        _add_readiness_unconfirmed_ban(connection)  # the "column already there" branch.

        columns = _readiness_columns(connection)
        assert columns["unconfirmed_bans"]["notnull"] == 1
        assert columns["unconfirmed_ban_at"]["notnull"] == 0

    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT unconfirmed_bans, unconfirmed_ban_at FROM neurocomment_readiness ORDER BY id",
        ).all()
    # Every pre-existing row: no refusals collected, no stamp. A pair carrying a count it
    # never earned would be banned on the first refusal after the upgrade.
    assert rows == [(0, None), (0, None)]


def test_migration_48_is_stamped() -> None:
    engine = _get_engine()
    with engine.connect() as connection:
        versions = {
            int(row[0]) for row in connection.exec_driver_sql("SELECT version FROM schema_version")
        }
    assert 48 in versions


def _legacy_overshot_budgets(legacy_engine: _EngineFactory, name: str) -> Engine:
    """Both counters as the 4 → 2 change left them: rows above, at, and below the new cap."""
    engine = _legacy_readiness(
        legacy_engine,
        name,
        ", rejoin_attempts INTEGER NOT NULL DEFAULT 0, rejoin_attempted_at VARCHAR",
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_readiness "
            "(account_id, channel, checked_at, rejoin_attempts, rejoin_attempted_at) VALUES "
            "('acc-1', '@chan', 'then', 4, 'last-week'),"  # spent the whole OLD budget
            "('acc-2', '@chan', 'then', 2, 'last-week'),"  # exactly at the new cap
            "('acc-3', '@chan', 'then', 1, 'yesterday'),"  # still inside the new budget
            "('acc-4', '@chan', 'then', 0, NULL)",  # never parked
        )
        connection.exec_driver_sql(
            "CREATE TABLE neurocomment_campaign_channels ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id VARCHAR NOT NULL, "
            "channel VARCHAR NOT NULL, active INTEGER NOT NULL, created_at VARCHAR NOT NULL, "
            "pause_rounds INTEGER NOT NULL DEFAULT 0, paused_until VARCHAR)",
        )
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_campaign_channels "
            "(campaign_id, channel, active, created_at, pause_rounds, paused_until) VALUES "
            "('c', '@over', 1, 'then', 3, 'last-week'),"
            "('c', '@at', 1, 'then', 2, 'last-week'),"
            "('c', '@under', 1, 'then', 1, 'last-week'),"
            "('c', '@clean', 1, 'then', 0, NULL)",
        )
    return engine


def test_migration_48_resets_the_budgets_that_overshot_the_new_cap(
    legacy_engine: _EngineFactory,
) -> None:
    """#48 gives back the full budget to every row the 4 → 2 change had already spent.

    Without it the first sweep after the upgrade unlinks six live channels: a row at 2 is
    instantly "exhausted" under the new cap, and its stamp is a week old, so the deferred
    verdict comes due in the same tick — no re-join under the new rule, and none of the 48h
    the rule promises.
    """
    engine = _legacy_overshot_budgets(legacy_engine, "budgets-pre-48.db")
    with engine.begin() as connection:
        _reset_overshot_retry_budgets(connection)
        _reset_overshot_retry_budgets(connection)  # idempotent — must not raise or re-fire.

    with engine.connect() as connection:
        readiness = connection.exec_driver_sql(
            "SELECT account_id, rejoin_attempts, rejoin_attempted_at "
            "FROM neurocomment_readiness ORDER BY id",
        ).all()
        links = connection.exec_driver_sql(
            "SELECT channel, pause_rounds, paused_until "
            "FROM neurocomment_campaign_channels ORDER BY id",
        ).all()
    # Counter AND stamp go together: a zeroed counter beside a week-old deadline would
    # still read as "this window already ran out". Rows below the new cap are untouched —
    # they have attempts left and their timeline is honest under the new rule.
    assert readiness == [
        ("acc-1", 0, None),
        ("acc-2", 0, None),
        ("acc-3", 1, "yesterday"),
        ("acc-4", 0, None),
    ]
    assert links == [
        ("@over", 0, None),
        ("@at", 0, None),
        ("@under", 1, "last-week"),
        ("@clean", 0, None),
    ]


def test_migration_48_skips_a_database_without_the_columns(
    legacy_engine: _EngineFactory,
) -> None:
    """Inert rather than raising mid-upgrade on a DB that somehow never got #42/#43."""
    engine = _legacy_readiness(legacy_engine, "budgets-no-columns.db")
    with engine.begin() as connection:
        _reset_overshot_retry_budgets(connection)  # no columns, no link table → no raise.


def test_migration_49_adds_captcha_giveup_columns() -> None:
    """The one-shot captcha retry and the terminal state it ends in (#49)."""
    engine = _get_engine()
    with engine.connect() as connection:
        columns = _readiness_columns(connection)
        versions = {
            int(row[0]) for row in connection.exec_driver_sql("SELECT version FROM schema_version")
        }
    # The #43/#47 shape again: a nullable stamp beside a NOT NULL flag. The flag is what
    # every other reader keys on (onboarding's refusal, both drop rules, the queue), so a
    # NULL there would make each of them decide for itself what "unknown" means.
    assert columns["captcha_retry_at"]["notnull"] == 0
    assert columns["captcha_gave_up"]["notnull"] == 1
    # ``create_all`` quotes the server default and the ALTER does not, so compare the value.
    assert str(columns["captcha_gave_up"]["dflt_value"]).strip("'") == "0"
    assert 49 in versions


def test_migration_49_alters_a_readiness_table_that_predates_the_columns(
    legacy_engine: _EngineFactory,
) -> None:
    """The only path that ever runs on the operator's database, on a real legacy one.

    Nobody may come out of the upgrade already terminal: ``captcha_gave_up`` reading 1 on a
    pre-existing row would take that pair out of its channel for good on evidence gathered
    before the column existed, and a non-NULL ``captcha_retry_at`` would spend the retry it
    never got. Same reasoning #43 and #47 are tested for, and the same second call proves
    the PRAGMA guard, since a re-run ALTER would raise.
    """
    engine = _legacy_readiness(legacy_engine, "readiness-pre-49.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_readiness (account_id, channel, checked_at) "
            "VALUES ('acc-1', '@chan', 'then'), ('acc-2', '@chan', 'then')",
        )
        assert "captcha_gave_up" not in _readiness_columns(connection)

        _add_readiness_captcha_giveup(connection)
        _add_readiness_captcha_giveup(connection)  # the "column already there" branch.

        columns = _readiness_columns(connection)
        assert columns["captcha_retry_at"]["notnull"] == 0
        assert columns["captcha_gave_up"]["notnull"] == 1

    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT captcha_retry_at, captcha_gave_up FROM neurocomment_readiness ORDER BY id",
        ).all()
    assert rows == [(None, 0), (None, 0)]
