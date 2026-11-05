"""Migration 109 — the pending cloud-password columns on ``accounts``, and create_all parity."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core._schema_tables import _accounts
from core.db import _metadata
from core.migration_steps_twofa_pending import _add_account_twofa_pending
from core.migrations import MIGRATIONS

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_STEP = _add_account_twofa_pending
_NEW = {"twofa_password_pending", "twofa_pending_at"}


def _columns(engine: Engine) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql("PRAGMA table_info(accounts)").mappings().all()
    return {str(row["name"]): str(row["type"]) for row in rows}


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _STEP]
    assert entries == [(109, "add_account_twofa_pending", _STEP)]
    versions = [entry[0] for entry in MIGRATIONS]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))


def test_a_legacy_accounts_table_gains_both_columns_and_the_step_is_idempotent(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("legacy.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE accounts (account_id VARCHAR PRIMARY KEY, twofa_password VARCHAR)",
        )
        connection.exec_driver_sql(
            "INSERT INTO accounts (account_id, twofa_password) VALUES ('a', 'old-secret')",
        )
        _STEP(connection)
        _STEP(connection)
        row = connection.exec_driver_sql(
            "SELECT twofa_password, twofa_password_pending, twofa_pending_at FROM accounts",
        ).one()

    # The existing password is untouched; nothing is pending for it.
    assert tuple(row) == ("old-secret", None, None)
    assert set(_columns(engine)) >= _NEW


def test_created_and_migrated_columns_match(legacy_engine: _EngineFactory) -> None:
    created = legacy_engine("created.db")
    _metadata.create_all(created)
    migrated = legacy_engine("migrated.db")
    with migrated.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE accounts (account_id VARCHAR PRIMARY KEY)")
        _STEP(connection)

    created_columns = _columns(created)
    migrated_columns = _columns(migrated)
    for name in _NEW:
        assert created_columns[name] == migrated_columns[name]
        assert name in {column.name for column in _accounts.columns}


def test_the_step_skips_a_database_without_the_table(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("empty.db")
    with engine.begin() as connection:
        _STEP(connection)

    assert _columns(engine) == {}


@pytest.mark.parametrize("column", sorted(_NEW))
def test_the_columns_are_nullable_text(column: str) -> None:
    assert _accounts.c[column].nullable is True
