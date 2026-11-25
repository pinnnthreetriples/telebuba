"""Migration 126 — the cloud-password rejection budget columns, and create_all parity."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core._schema_tables import _accounts
from core.db import _metadata
from core.migration_steps_twofa_attempts import _add_account_twofa_attempts
from core.migrations import MIGRATIONS, apply_migrations

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_STEP = _add_account_twofa_attempts
_COLUMNS = {"twofa_rejected_count": "INTEGER", "twofa_rejected_since": "VARCHAR"}


def _columns(engine: Engine) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql("PRAGMA table_info(accounts)").mappings().all()
    return {str(row["name"]): str(row["type"]) for row in rows}


def test_the_registry_carries_the_migration_last_and_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _STEP]
    assert entries == [(126, "add_account_twofa_attempts", _STEP)]
    assert MIGRATIONS[-1][0] == 126
    versions = [entry[0] for entry in MIGRATIONS]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))


def test_a_legacy_accounts_table_gains_the_columns_and_the_step_is_idempotent(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("legacy.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE accounts (account_id VARCHAR PRIMARY KEY, twofa_password VARCHAR)"
        )
        connection.exec_driver_sql(
            "INSERT INTO accounts (account_id, twofa_password) VALUES ('a', 'pw')"
        )
        _STEP(connection)
        _STEP(connection)
        row = connection.exec_driver_sql(
            "SELECT twofa_password, twofa_rejected_count, twofa_rejected_since FROM accounts"
        ).one()

    # A fresh budget for every existing account: nothing is backfilled.
    assert tuple(row) == ("pw", None, None)
    assert _COLUMNS.items() <= _columns(engine).items()


def test_the_registry_adds_the_columns_to_a_database_without_them_and_reapplies(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("twice.db")
    _metadata.create_all(engine)
    with engine.begin() as connection:
        for name in _COLUMNS:
            connection.exec_driver_sql(f"ALTER TABLE accounts DROP COLUMN {name}")
    assert not set(_COLUMNS) & set(_columns(engine))

    apply_migrations(engine)
    apply_migrations(engine)

    assert _COLUMNS.items() <= _columns(engine).items()


def test_a_database_created_fresh_matches_a_migrated_one(legacy_engine: _EngineFactory) -> None:
    created = legacy_engine("created.db")
    _metadata.create_all(created)
    migrated = legacy_engine("migrated.db")
    with migrated.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE accounts (account_id VARCHAR PRIMARY KEY)")
        _STEP(connection)

    for name in _COLUMNS:
        assert _columns(created)[name] == _columns(migrated)[name]
        assert _accounts.c[name].nullable is True


def test_the_step_skips_a_database_without_the_table(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("empty.db")
    with engine.begin() as connection:
        _STEP(connection)

    assert _columns(engine) == {}
