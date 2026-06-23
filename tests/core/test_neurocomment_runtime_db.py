"""Tests for the persisted neurocomment listener-account scalar (issue #119)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import inspect

from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    configure_database,
    get_listener_account_id,
    set_listener_account_id,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


def test_runtime_table_created_and_migration_stamped() -> None:
    engine = _get_engine()
    assert inspect(engine).has_table("neurocomment_runtime")
    with engine.connect() as connection:
        versions = {
            int(row[0]) for row in connection.exec_driver_sql("SELECT version FROM schema_version")
        }
    assert 12 in versions


@pytest.mark.asyncio
async def test_listener_id_defaults_to_none() -> None:
    assert await get_listener_account_id() is None


@pytest.mark.asyncio
async def test_set_then_get_listener_id() -> None:
    await set_listener_account_id("acc-1")
    assert await get_listener_account_id() == "acc-1"


@pytest.mark.asyncio
async def test_set_overwrites_single_row() -> None:
    await set_listener_account_id("acc-1")
    await set_listener_account_id("acc-2")
    assert await get_listener_account_id() == "acc-2"
    # Single-row invariant: only the pinned id=1 row ever exists.
    with _get_engine().connect() as connection:
        count = connection.exec_driver_sql("SELECT COUNT(*) FROM neurocomment_runtime").scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_clear_listener_id() -> None:
    await set_listener_account_id("acc-1")
    await set_listener_account_id(None)
    assert await get_listener_account_id() is None
