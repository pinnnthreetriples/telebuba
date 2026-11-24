"""Migration 121 — ``accounts.twofa_pending_refused_at``, and create_all parity."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core._schema_tables import _accounts
from core.db import _metadata
from core.migration_steps_twofa_refused import _add_account_twofa_refused
from core.migrations import MIGRATIONS, apply_migrations

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_STEP = _add_account_twofa_refused
_COLUMN = "twofa_pending_refused_at"


def _columns(engine: Engine) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql("PRAGMA table_info(accounts)").mappings().all()
    return {str(row["name"]): str(row["type"]) for row in rows}


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _STEP]
    assert entries == [(121, "add_account_twofa_refused", _STEP)]
    versions = [entry[0] for entry in MIGRATIONS]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))


def test_a_legacy_accounts_table_gains_the_column_and_the_step_is_idempotent(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("legacy.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE accounts ("
            "account_id VARCHAR PRIMARY KEY, twofa_password_pending VARCHAR)",
        )
        connection.exec_driver_sql(
            "INSERT INTO accounts (account_id, twofa_password_pending) VALUES ('a', 'seller-pw')",
        )
        _STEP(connection)
        _STEP(connection)
        row = connection.exec_driver_sql(
            f"SELECT twofa_password_pending, {_COLUMN} FROM accounts",  # noqa: S608 - constant
        ).one()

    # An existing candidate is untouched and carries no refusal.
    assert tuple(row) == ("seller-pw", None)
    assert _COLUMN in _columns(engine)


def test_the_registry_adds_the_column_to_a_database_without_it_and_reapplies(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("twice.db")
    _metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(f"ALTER TABLE accounts DROP COLUMN {_COLUMN}")
    assert _COLUMN not in _columns(engine)

    apply_migrations(engine)
    apply_migrations(engine)

    assert _COLUMN in _columns(engine)


def test_a_database_created_fresh_matches_a_migrated_one(legacy_engine: _EngineFactory) -> None:
    created = legacy_engine("created.db")
    _metadata.create_all(created)
    apply_migrations(created)
    migrated = legacy_engine("migrated.db")
    with migrated.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE accounts (account_id VARCHAR PRIMARY KEY)")
        _STEP(connection)

    assert _columns(created)[_COLUMN] == _columns(migrated)[_COLUMN]
    assert _accounts.c[_COLUMN].nullable is True


def test_the_step_skips_a_database_without_the_table(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("empty.db")
    with engine.begin() as connection:
        _STEP(connection)

    assert _columns(engine) == {}
