"""Migration #79 — ``warming_account_state.spam_unknown_streak``.

Its own module because ``tests/core/test_migrations.py`` is at the test source cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    _warming_account_state,
    configure_database,
)
from core.migration_steps_spam_streak import _add_warming_state_spam_unknown_streak
from core.migrations import MIGRATIONS

if TYPE_CHECKING:
    from pathlib import Path

    from tests.core.conftest import _EngineFactory

_STEP = _add_warming_state_spam_unknown_streak


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


def _columns(engine: object, table: str) -> set[str]:
    with engine.connect() as connection:  # ty: ignore[unresolved-attribute]
        rows = connection.exec_driver_sql(f"PRAGMA table_info({table})").mappings().all()
    return {str(row["name"]) for row in rows}


def _create_legacy_state(engine: object) -> None:
    with engine.begin() as connection:  # ty: ignore[unresolved-attribute]
        connection.exec_driver_sql(
            "CREATE TABLE warming_account_state ("
            "account_id VARCHAR PRIMARY KEY, state VARCHAR NOT NULL, "
            "cycles_completed INTEGER NOT NULL, updated_at VARCHAR NOT NULL)",
        )
        connection.exec_driver_sql(
            "INSERT INTO warming_account_state "
            "(account_id, state, cycles_completed, updated_at) "
            "VALUES ('acc-legacy', 'sleeping', 3, '2026-01-01T00:00:00+00:00')",
        )


def test_a_legacy_row_gains_a_zero_streak(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("legacy.db")
    _create_legacy_state(engine)
    with engine.begin() as connection:
        _STEP(connection)
        stored = connection.exec_driver_sql(
            "SELECT spam_unknown_streak FROM warming_account_state",
        ).scalar()

    assert stored == 0


def test_the_step_is_idempotent_and_registered(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("twice.db")
    _create_legacy_state(engine)
    with engine.begin() as connection:
        _STEP(connection)
        _STEP(connection)

    assert "spam_unknown_streak" in _columns(engine, "warming_account_state")
    assert (79, "add_warming_state_spam_unknown_streak", _STEP) in MIGRATIONS
    assert "spam_unknown_streak" in _columns(_get_engine(), "warming_account_state")
    assert "spam_unknown_streak" in {column.name for column in _warming_account_state.columns}


def test_the_step_skips_a_database_without_the_table(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("empty.db")
    with engine.begin() as connection:
        _STEP(connection)

    assert _columns(engine, "warming_account_state") == set()
