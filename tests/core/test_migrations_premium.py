"""Migration #59 — the ``accounts.premium`` column.

Its own module because ``tests/core/test_migrations.py`` is at the 700-line test
source cap (``tests.test_architecture._TEST_FILE_MAX_LINES``), like #57's file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import _accounts, _get_engine, configure_database  # type: ignore[attr-defined]
from core.migration_steps import _add_account_premium
from core.migrations import MIGRATIONS

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


def _create_legacy_accounts(engine: object) -> None:
    with engine.begin() as connection:  # ty: ignore[unresolved-attribute]
        connection.exec_driver_sql(
            "CREATE TABLE accounts ("
            "account_id VARCHAR PRIMARY KEY, status VARCHAR NOT NULL, "
            "created_at VARCHAR NOT NULL, updated_at VARCHAR NOT NULL)",
        )
        connection.exec_driver_sql(
            "INSERT INTO accounts (account_id, status, created_at, updated_at) "
            "VALUES ('acc-legacy', 'alive', '2026-01-01T00:00:00+00:00', "
            "'2026-01-01T00:00:00+00:00')",
        )


def test_a_legacy_accounts_table_gains_the_column(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("legacy.db")
    _create_legacy_accounts(engine)
    with engine.begin() as connection:
        _add_account_premium(connection)
        stored = connection.exec_driver_sql(
            "SELECT premium FROM accounts WHERE account_id = 'acc-legacy'",
        ).scalar()

    assert stored is None
    assert "premium" in _columns(engine, "accounts")


def test_the_step_is_idempotent_and_registered(legacy_engine: _EngineFactory) -> None:
    """The step survives a rerun, sits in the registry, and matches ``create_all``."""
    engine = legacy_engine("twice.db")
    _create_legacy_accounts(engine)
    with engine.begin() as connection:
        _add_account_premium(connection)
        _add_account_premium(connection)

    assert "premium" in _columns(engine, "accounts")
    assert (59, "add_account_premium", _add_account_premium) in MIGRATIONS
    # ``_isolate_db`` built the fresh route (``create_all`` + the whole registry).
    assert "premium" in _columns(_get_engine(), "accounts")
    assert "premium" in {column.name for column in _accounts.columns}


def test_the_step_skips_a_database_without_an_accounts_table(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("no-accounts.db")
    with engine.begin() as connection:
        _add_account_premium(connection)

    assert _columns(engine, "accounts") == set()
