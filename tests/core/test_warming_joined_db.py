"""``warming_joined_channels.left_at`` — joined-and-not-left, the re-join upsert, migration #63.

The migration cases live here for the reason ``test_warming_extras_db.py`` gives:
``tests/core/test_migrations.py`` sits at the test source cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select, update

from core.db import (
    _get_engine,
    _warming_joined_channels,
    configure_database,
    is_channel_joined,
    list_joined_channels,
    record_channel_joined,
    record_channel_left,
)
from core.migration_steps_warming_extras import _add_warming_joined_left_at
from core.migrations import MIGRATIONS
from schemas._warming_extras import JoinedChannel
from tests.core.test_warming_extras_db import _columns

if TYPE_CHECKING:
    from pathlib import Path

    from tests.core.conftest import _EngineFactory

_TABLE = "warming_joined_channels"
_OLD = "2026-01-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


def _backdate(account_id: str, channel: str, created_at: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_warming_joined_channels)
            .where(
                (_warming_joined_channels.c.account_id == account_id)
                & (_warming_joined_channels.c.channel == channel),
            )
            .values(created_at=created_at),
        )


@pytest.mark.asyncio
async def test_a_left_channel_is_no_longer_joined_but_keeps_its_row() -> None:
    await record_channel_joined("acc", "chan")
    assert await is_channel_joined("acc", "chan") is True

    await record_channel_left("acc", "chan")

    assert await is_channel_joined("acc", "chan") is False
    (row,) = await list_joined_channels("acc")
    assert isinstance(row, JoinedChannel)
    assert row.channel == "chan"
    assert row.left_at is not None


@pytest.mark.asyncio
async def test_a_rejoin_clears_left_at_and_restarts_the_joined_clock() -> None:
    await record_channel_joined("acc", "chan")
    _backdate("acc", "chan", _OLD)
    await record_channel_left("acc", "chan")

    await record_channel_joined("acc", "chan")

    (row,) = await list_joined_channels("acc")
    assert row.left_at is None
    assert row.created_at > _OLD  # ISO-8601 sorts chronologically
    assert await is_channel_joined("acc", "chan") is True


@pytest.mark.asyncio
async def test_a_repeated_join_of_a_joined_channel_is_one_row() -> None:
    await record_channel_joined("acc", "chan")
    await record_channel_joined("acc", "chan")
    with _get_engine().connect() as connection:
        assert len(connection.execute(select(_warming_joined_channels)).all()) == 1


@pytest.mark.asyncio
async def test_list_is_per_account_and_includes_left_rows() -> None:
    await record_channel_joined("acc", "a")
    await record_channel_joined("acc", "b")
    await record_channel_joined("other", "a")
    await record_channel_left("acc", "b")

    rows = sorted(await list_joined_channels("acc"), key=lambda row: row.channel)

    assert [(row.channel, row.left_at is None) for row in rows] == [("a", True), ("b", False)]
    assert await list_joined_channels("nobody") == []


@pytest.mark.asyncio
async def test_leaving_an_unknown_channel_writes_nothing() -> None:
    await record_channel_left("acc", "ghost")
    assert await list_joined_channels("acc") == []


# --- migration #63 -----------------------------------------------------------


def _create_legacy_joined(engine: object) -> None:
    with engine.begin() as connection:  # ty: ignore[unresolved-attribute]
        connection.exec_driver_sql(
            "CREATE TABLE warming_joined_channels ("
            "account_id VARCHAR NOT NULL, channel VARCHAR NOT NULL, "
            "created_at VARCHAR NOT NULL, PRIMARY KEY (account_id, channel))",
        )
        connection.exec_driver_sql(
            "INSERT INTO warming_joined_channels VALUES ('acc', 'chan', '2026-01-01')",
        )


def test_the_step_is_idempotent_registered_and_keeps_legacy_rows_joined(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("legacy.db")
    _create_legacy_joined(engine)
    with engine.begin() as connection:
        _add_warming_joined_left_at(connection)
        _add_warming_joined_left_at(connection)

    assert "left_at" in _columns(engine, _TABLE)
    with engine.connect() as connection:
        rows = connection.exec_driver_sql("SELECT left_at FROM warming_joined_channels").all()
    assert rows == [(None,)]  # NULL = still joined: a deploy leaves the fleet as it was
    assert (63, "add_warming_joined_left_at", _add_warming_joined_left_at) in MIGRATIONS
    # ``_isolate_db`` built the fresh route (``create_all`` + the whole registry).
    assert "left_at" in _columns(_get_engine(), _TABLE)


def test_the_step_skips_a_database_without_the_table(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("no-joined.db")
    with engine.begin() as connection:
        _add_warming_joined_left_at(connection)
    assert _columns(engine, _TABLE) == set()
