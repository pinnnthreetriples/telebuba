"""``warming_settings.extra_toggles`` — JSON column, per-key merge, and migration #62.

The migration cases live here rather than in ``tests/core/test_migrations.py``, which
sits at the 700-line test source cap (``tests.test_architecture._TEST_FILE_MAX_LINES``).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select, update

from core.db import (
    _get_engine,
    _warming_settings,
    configure_database,
    load_warming_settings,
    save_warming_settings,
)
from core.migration_steps_warming_extras import _add_warming_settings_extra_toggles
from core.migrations import MIGRATIONS
from core.repositories._warming_settings import _invalidate_warming_settings_cache
from schemas._warming_extras import EXTRA_TOGGLE_DEFAULTS

if TYPE_CHECKING:
    from pathlib import Path

    from tests.core.conftest import _EngineFactory


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


def _raw_column() -> str | None:
    with _get_engine().connect() as connection:
        return connection.execute(select(_warming_settings.c.extra_toggles)).scalar_one()


def _write_raw_column(value: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(update(_warming_settings).values(extra_toggles=value))
    _invalidate_warming_settings_cache()


@pytest.mark.asyncio
async def test_null_column_reads_as_the_defaults() -> None:
    secret = await load_warming_settings()
    assert secret.extra_toggles == EXTRA_TOGGLE_DEFAULTS
    assert _raw_column() is None


@pytest.mark.asyncio
async def test_partial_update_merges_and_keeps_untouched_keys() -> None:
    first = await save_warming_settings(gemini_api_key=None, extra_toggles={"polls": True})
    assert first.extra_toggles == {**EXTRA_TOGGLE_DEFAULTS, "polls": True}

    second = await save_warming_settings(gemini_api_key=None, extra_toggles={"dialogs": False})
    assert second.extra_toggles == {**EXTRA_TOGGLE_DEFAULTS, "polls": True, "dialogs": False}
    # Only the keys ever written are stored, sorted, so equal states are byte-equal.
    assert _raw_column() == json.dumps({"dialogs": False, "polls": True}, sort_keys=True)


@pytest.mark.asyncio
async def test_none_leaves_the_column_untouched() -> None:
    await save_warming_settings(gemini_api_key=None, extra_toggles={"polls": True})
    kept = await save_warming_settings(gemini_api_key=None, reactions_enabled=False)
    assert kept.extra_toggles["polls"] is True
    assert kept.reactions_enabled is False


@pytest.mark.asyncio
async def test_unknown_stored_key_is_dropped_on_read_and_kept_on_write() -> None:
    await load_warming_settings()  # seed the singleton row
    _write_raw_column(json.dumps({"ghost": True, "polls": True}))

    loaded = await load_warming_settings()
    assert "ghost" not in loaded.extra_toggles
    assert loaded.extra_toggles["polls"] is True

    await save_warming_settings(gemini_api_key=None, extra_toggles={"mute": True})
    assert json.loads(_raw_column() or "{}") == {"ghost": True, "mute": True, "polls": True}


@pytest.mark.asyncio
async def test_non_object_json_in_the_column_reads_as_the_defaults() -> None:
    await load_warming_settings()  # seed the singleton row
    _write_raw_column("[]")
    assert (await load_warming_settings()).extra_toggles == EXTRA_TOGGLE_DEFAULTS


@pytest.mark.asyncio
async def test_cache_is_invalidated_after_a_save() -> None:
    cached = await load_warming_settings()
    assert cached.extra_toggles["polls"] is False
    await save_warming_settings(gemini_api_key=None, extra_toggles={"polls": True})
    assert (await load_warming_settings()).extra_toggles["polls"] is True


# --- migration #62 -----------------------------------------------------------


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


def test_the_step_is_idempotent_and_registered(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("legacy.db")
    _create_legacy_settings(engine)
    with engine.begin() as connection:
        _add_warming_settings_extra_toggles(connection)
        _add_warming_settings_extra_toggles(connection)

    assert "extra_toggles" in _columns(engine, "warming_settings")
    assert (
        62,
        "add_warming_settings_extra_toggles",
        _add_warming_settings_extra_toggles,
    ) in MIGRATIONS
    # ``_isolate_db`` built the fresh route (``create_all`` + the whole registry).
    assert "extra_toggles" in _columns(_get_engine(), "warming_settings")


def test_the_step_skips_a_database_without_the_settings_table(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("no-settings.db")
    with engine.begin() as connection:
        _add_warming_settings_extra_toggles(connection)
    assert _columns(engine, "warming_settings") == set()
