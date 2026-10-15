"""Migration 74 — the lifecycle journal's schema and its backfill from ``logs``.

Covers parity with ``create_all``, the event mapping, the guessed delete reasons and
idempotence.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from core.db import _metadata
from core.migration_steps_lifecycle import _add_account_lifecycle_events
from core.migration_steps_spam_lift import _allow_lifecycle_spam_block_lifted
from core.migration_steps_waves import _allow_lifecycle_warming_stopped
from core.migrations import MIGRATIONS, apply_migrations
from tests.core.test_migrations_scheduled_posts import _checks, _shape

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_TABLE = "account_lifecycle_events"
_SOURCES = ("accounts", "logs", "warming_account_state")


def _create_sources(engine: Engine) -> None:
    for name in _SOURCES:
        _metadata.tables[name].create(engine)


def _log(engine: Engine, account_id: str | None, event: str, extra: object = None) -> int:
    raw = extra if isinstance(extra, str) else json.dumps(extra or {})
    with engine.begin() as connection:
        cursor = connection.exec_driver_sql(
            "INSERT INTO logs (created_at, level, status, account_id, event, extra)"
            " VALUES (?, 'INFO', 'ok', ?, ?, ?)",
            (datetime.now(UTC).isoformat(), account_id, event, raw),
        )
        return int(cursor.lastrowid or 0)


def _rows(engine: Engine) -> list[dict[str, object]]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT account_id, tg_user_id, stint_started_at, kind, extra, backfilled,"
            " source_log_id FROM account_lifecycle_events ORDER BY id"
        )
        return [dict(row) for row in rows.mappings()]


@pytest.fixture
def created_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("created.db")
    _metadata.create_all(engine)
    return engine


@pytest.fixture
def migrated_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("migrated.db")
    _create_sources(engine)
    with engine.begin() as connection:
        _add_account_lifecycle_events(connection)
    return engine


def _migrate(engine: Engine) -> None:
    with engine.begin() as connection:
        _add_account_lifecycle_events(connection)


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_account_lifecycle_events]
    assert entries == [(74, "add_account_lifecycle_events", _add_account_lifecycle_events)]


def test_created_and_migrated_schemas_match(
    created_engine: Engine, migrated_engine: Engine
) -> None:
    # 95 and 96 widened the kind CHECK by rebuilding the table; 74 + 95 + 96 is today's.
    with migrated_engine.begin() as connection:
        _allow_lifecycle_warming_stopped(connection)
        _allow_lifecycle_spam_block_lifted(connection)
    assert _shape(created_engine, _TABLE) == _shape(migrated_engine, _TABLE)
    assert any("'session_dead'" in check for check in _checks(created_engine, _TABLE))


def test_the_journal_has_no_foreign_key_to_accounts(created_engine: Engine) -> None:
    assert _shape(created_engine, _TABLE)["keys"] == []


def test_a_legacy_db_without_the_source_tables_only_gets_the_table(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("bare.db")
    _migrate(engine)
    assert _rows(engine) == []


def test_backfill_maps_each_log_event_to_its_kind(migrated_engine: Engine) -> None:
    engine = migrated_engine
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO accounts (account_id, status, user_id, created_at, updated_at)"
            " VALUES ('a1', 'alive', 777, '2000-01-01', '2000-01-01')"
        )
    start = _log(engine, "a1", "warming_started")
    _log(engine, "a1", "warming_cycle_completed", {"status": "ok"})
    _log(engine, "a1", "warming_cycle_completed", {"status": "peer_flood"})
    _log(engine, "a1", "spam_status_refreshed", {"status": "limited", "detail": "until"})
    _log(engine, "a1", "warming_target_reached", {"target_days": 3})
    _log(engine, "a1", "warming_promoted_to_neurocomment")
    _log(engine, "a1", "warming_nc_handoff")
    _log(engine, "a1", "proxy_checked")
    _log(engine, None, "warming_started")

    _migrate(engine)

    rows = _rows(engine)
    assert [row["kind"] for row in rows] == [
        "warming_started",
        "quarantined",
        "spambot_verdict",
        "warmed",
        "promoted",
        "handed_off",
    ]
    started_at = rows[0]["stint_started_at"]
    assert all(row["stint_started_at"] == started_at for row in rows)
    assert all(row["tg_user_id"] == 777 for row in rows)
    assert all(row["backfilled"] == 1 for row in rows)
    assert rows[0]["source_log_id"] == start
    assert json.loads(str(rows[2]["extra"])) == {"status": "limited", "detail": "until"}
    assert json.loads(str(rows[1]["extra"])) == {"status": "peer_flood"}


def test_backfill_guesses_the_reason_of_old_deletions(migrated_engine: Engine) -> None:
    engine = migrated_engine
    _log(engine, "dead", "spam_status_refreshed", {"status": "limited"})
    _log(engine, "dead", "account_removed")
    _log(engine, "kept", "spam_status_refreshed", {"status": "clean"})
    _log(engine, "kept", "account_removed")
    # A limited verdict AFTER the delete belongs to a re-imported account's new life.
    _log(engine, "kept", "spam_status_refreshed", {"status": "limited"})

    _migrate(engine)

    deleted = {
        row["account_id"]: json.loads(str(row["extra"]))
        for row in _rows(engine)
        if row["kind"] == "deleted"
    }
    assert deleted == {
        "dead": {"reason": "dead", "reason_source": "guess"},
        "kept": {"reason": "retired", "reason_source": "guess"},
    }


def test_backfill_does_not_lend_a_reimported_row_to_an_older_life(
    migrated_engine: Engine,
) -> None:
    engine = migrated_engine
    _log(engine, "re", "warming_started")
    _log(engine, "re", "account_removed")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO accounts (account_id, status, user_id, created_at, updated_at)"
            " VALUES ('re', 'alive', 999, '2000-01-01', '2000-01-01')"
        )
        connection.exec_driver_sql(
            "INSERT INTO warming_account_state (account_id, state, cycles_completed,"
            " updated_at, started_at) VALUES ('re', 'active', 0, 'x', 'new-stint')"
        )

    _migrate(engine)

    assert [(row["kind"], row["tg_user_id"]) for row in _rows(engine)] == [
        ("warming_started", None),
        ("deleted", None),
    ]


def test_backfill_keeps_each_life_to_its_own_history(migrated_engine: Engine) -> None:
    """A second life of the same id inherits neither the first life's stint nor its death."""
    engine = migrated_engine
    _log(engine, "re", "warming_started")
    _log(engine, "re", "spam_status_refreshed", {"status": "limited"})
    _log(engine, "re", "account_removed")
    _log(engine, "re", "warming_nc_handoff")
    _log(engine, "re", "account_removed")

    _migrate(engine)

    rows = _rows(engine)
    assert [row["kind"] for row in rows] == [
        "warming_started",
        "spambot_verdict",
        "deleted",
        "handed_off",
        "deleted",
    ]
    assert json.loads(str(rows[2]["extra"]))["reason"] == "dead"
    assert rows[3]["stint_started_at"] is None
    assert json.loads(str(rows[4]["extra"]))["reason"] == "retired"


def test_backfill_never_lends_an_account_created_after_the_event(
    migrated_engine: Engine,
) -> None:
    """The ``account_removed`` row may be purged; a younger row still must not lend its id."""
    engine = migrated_engine
    _log(engine, "re", "warming_started")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO accounts (account_id, status, user_id, created_at, updated_at)"
            " VALUES ('re', 'alive', 999, '9999-01-01', '9999-01-01')"
        )

    _migrate(engine)

    [row] = _rows(engine)
    assert row["tg_user_id"] is None


def test_backfill_falls_back_to_the_live_stint_and_tolerates_broken_json(
    migrated_engine: Engine,
) -> None:
    engine = migrated_engine
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO accounts (account_id, status, created_at, updated_at)"
            " VALUES ('a1', 'alive', '2000-01-01', '2000-01-01')"
        )
        connection.exec_driver_sql(
            "INSERT INTO warming_account_state (account_id, state, cycles_completed,"
            " updated_at, started_at) VALUES ('a1', 'active', 0, 'x', 'stint-1')"
        )
    _log(engine, "a1", "spam_status_refreshed", "{not json")
    _log(engine, "a1", "warming_cycle_completed", "{not json")

    _migrate(engine)

    rows = _rows(engine)
    assert [row["kind"] for row in rows] == ["spambot_verdict"]
    assert rows[0]["stint_started_at"] == "stint-1"
    assert json.loads(str(rows[0]["extra"])) == {"status": None, "detail": None}


def test_backfill_is_idempotent(migrated_engine: Engine) -> None:
    engine = migrated_engine
    _log(engine, "a1", "warming_started")
    _log(engine, "a1", "account_removed")
    _migrate(engine)
    first = _rows(engine)

    _migrate(engine)

    assert _rows(engine) == first
    assert len(first) == 2


def test_apply_migrations_backfills_an_existing_database(legacy_engine: _EngineFactory) -> None:
    """A database stamped through #73 with logs already in it gets #74 and its backfill."""
    engine = legacy_engine("existing.db")
    _metadata.create_all(engine, tables=[t for t in _metadata.sorted_tables if t.name != _TABLE])
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY,"
            " name VARCHAR NOT NULL, applied_at VARCHAR NOT NULL)"
        )
        for version, name, _body in MIGRATIONS:
            if version < 74:
                connection.exec_driver_sql(
                    "INSERT INTO schema_version VALUES (?, ?, 'x')", (version, name)
                )
    _log(engine, "a1", "warming_started")

    apply_migrations(engine)
    apply_migrations(engine)

    assert [row["kind"] for row in _rows(engine)] == ["warming_started"]
    with engine.connect() as connection:
        stamped = connection.exec_driver_sql(
            "SELECT count(*) FROM schema_version WHERE version = 74"
        ).scalar_one()
    assert stamped == 1
