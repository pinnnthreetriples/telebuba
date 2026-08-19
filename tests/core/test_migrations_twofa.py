"""Migration #57 — the ``accounts.twofa_password`` column.

Its own module because ``tests/core/test_migrations.py`` is at the 700-line test
source cap (``tests.test_architecture._TEST_FILE_MAX_LINES``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import configure_database  # type: ignore[attr-defined]
from core.migration_steps import _add_account_twofa_password

if TYPE_CHECKING:
    from pathlib import Path

    from tests.core.conftest import _EngineFactory


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


def _columns(engine: object, table: str) -> set[str]:
    with engine.connect() as connection:  # ty: ignore[unresolved-attribute]
        rows = connection.exec_driver_sql(f"PRAGMA table_info({table})").mappings().all()
    return {str(row["name"]) for row in rows}


def test_a_legacy_accounts_table_gains_the_column(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("legacy.db")
    now = "2026-01-01T00:00:00+00:00"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE accounts ("
            "account_id VARCHAR PRIMARY KEY, status VARCHAR NOT NULL, "
            "created_at VARCHAR NOT NULL, updated_at VARCHAR NOT NULL)",
        )
        connection.exec_driver_sql(
            "INSERT INTO accounts (account_id, status, created_at, updated_at) "
            "VALUES ('acc-legacy', 'alive', ?, ?)",
            (now, now),
        )
        _add_account_twofa_password(connection)
        stored = connection.exec_driver_sql(
            "SELECT twofa_password FROM accounts WHERE account_id = 'acc-legacy'",
        ).scalar()

    assert stored is None
    assert "twofa_password" in _columns(engine, "accounts")


def test_the_step_is_idempotent(legacy_engine: _EngineFactory) -> None:
    """Every migration body must survive a second run.

    SQLite has no ``IF NOT EXISTS`` for ``ADD COLUMN``, so the column-name check IS
    the guard, and this is what proves it.
    """
    engine = legacy_engine("twice.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE accounts ("
            "account_id VARCHAR PRIMARY KEY, status VARCHAR NOT NULL, "
            "created_at VARCHAR NOT NULL, updated_at VARCHAR NOT NULL)",
        )
        _add_account_twofa_password(connection)
        _add_account_twofa_password(connection)

    assert "twofa_password" in _columns(engine, "accounts")


def test_the_step_skips_a_database_without_an_accounts_table(
    legacy_engine: _EngineFactory,
) -> None:
    """The registry runs over partial databases too — see the step's own comment."""
    engine = legacy_engine("no-accounts.db")
    with engine.begin() as connection:
        _add_account_twofa_password(connection)

    assert _columns(engine, "accounts") == set()
