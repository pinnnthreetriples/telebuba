"""Migrations #64-#66 — dead ``warming_settings`` columns out, new LLM columns in.

The new columns are the DeepSeek key and the text-LLM choice.

Its own module because ``tests/core/test_migrations.py`` is at the 700-line test
source cap (``tests.test_architecture._TEST_FILE_MAX_LINES``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import _get_engine, _warming_settings, configure_database  # type: ignore[attr-defined]
from core.migration_steps import _add_warming_user_controls
from core.migration_steps_llm_keys import (
    _add_warming_settings_deepseek_key,
    _add_warming_settings_text_llm_provider,
    _drop_warming_settings_dead_columns,
)
from core.migrations import MIGRATIONS

if TYPE_CHECKING:
    from pathlib import Path

    from tests.core.conftest import _EngineFactory

_DEAD = {"quiet_hours_enabled", "quiet_hours_start", "quiet_hours_end", "max_daily_actions"}


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


def _columns(engine: object, table: str) -> set[str]:
    with engine.connect() as connection:  # ty: ignore[unresolved-attribute]
        rows = connection.exec_driver_sql(f"PRAGMA table_info({table})").mappings().all()
    return {str(row["name"]) for row in rows}


def _create_legacy_settings(engine: object) -> None:
    with engine.begin() as connection:  # ty: ignore[unresolved-attribute]
        connection.exec_driver_sql(
            "CREATE TABLE warming_settings ("
            "id INTEGER PRIMARY KEY, inter_account_chat INTEGER NOT NULL, "
            "reactions_enabled INTEGER NOT NULL, gemini_api_key VARCHAR NOT NULL, "
            "gemini_model VARCHAR NOT NULL, updated_at VARCHAR NOT NULL)",
        )
        connection.exec_driver_sql(
            "INSERT INTO warming_settings VALUES (1, 1, 0, 'g-key', 'm', '2026-01-01')",
        )
        _add_warming_user_controls(connection)


def test_a_legacy_row_loses_the_dead_columns_and_keeps_its_data(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("legacy.db")
    _create_legacy_settings(engine)
    assert _columns(engine, "warming_settings") >= _DEAD

    with engine.begin() as connection:
        _drop_warming_settings_dead_columns(connection)
        _add_warming_settings_deepseek_key(connection)
        _add_warming_settings_text_llm_provider(connection)
        row = connection.exec_driver_sql(
            "SELECT inter_account_chat, gemini_api_key, deepseek_api_key, text_llm_provider "
            "FROM warming_settings",
        ).one()

    columns = _columns(engine, "warming_settings")
    assert not _DEAD & columns
    assert {"deepseek_api_key", "text_llm_provider"} <= columns
    # NULL, which reads as the default provider — how an old row was always routed.
    assert tuple(row) == (1, "g-key", None, None)


def test_the_steps_are_idempotent_registered_and_match_create_all(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("twice.db")
    _create_legacy_settings(engine)
    with engine.begin() as connection:
        for _ in range(2):
            _drop_warming_settings_dead_columns(connection)
            _add_warming_settings_deepseek_key(connection)
            _add_warming_settings_text_llm_provider(connection)

    assert (64, "drop_warming_settings_dead_columns", _drop_warming_settings_dead_columns) in (
        MIGRATIONS
    )
    assert (65, "add_warming_settings_deepseek_key", _add_warming_settings_deepseek_key) in (
        MIGRATIONS
    )
    assert (
        66,
        "add_warming_settings_text_llm_provider",
        _add_warming_settings_text_llm_provider,
    ) in MIGRATIONS
    # ``_isolate_db`` built the fresh route (``create_all`` + the whole registry, whose
    # #5 re-adds the dead columns before #64 drops them again).
    fresh = _columns(_get_engine(), "warming_settings")
    assert fresh == {column.name for column in _warming_settings.columns}
    assert not _DEAD & fresh


def test_the_steps_skip_a_database_without_the_table(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("empty.db")
    with engine.begin() as connection:
        _drop_warming_settings_dead_columns(connection)
        _add_warming_settings_deepseek_key(connection)
        _add_warming_settings_text_llm_provider(connection)

    assert _columns(engine, "warming_settings") == set()
