"""Migration body for the inactive-channel rule's column (#51).

Own module because ``test_migrations`` sits at the 700-line test cap, which is also why
``core.migration_steps_channel_activity`` is its own module on the other side.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import configure_database  # type: ignore[attr-defined]

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Connection

    from tests.core.conftest import _EngineFactory


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


def _legacy_links(connection: Connection) -> None:
    """The pre-#51 table: no activity column, one linked channel in it."""
    connection.exec_driver_sql(
        "CREATE TABLE neurocomment_campaign_channels ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id VARCHAR NOT NULL, "
        "channel VARCHAR NOT NULL, active INTEGER NOT NULL, created_at VARCHAR NOT NULL)",
    )
    connection.exec_driver_sql(
        "INSERT INTO neurocomment_campaign_channels(campaign_id, channel, active, created_at) "
        "VALUES ('c1', '@news', 1, '2026-01-01T00:00:00+00:00')",
    )


def test_last_post_at_is_added_and_left_null_on_existing_links(
    legacy_engine: _EngineFactory,
) -> None:
    """NULL, never backfilled with "now".

    A backfill would claim we saw a post we never saw, and hand every existing channel a
    fresh week before the rule could look at it — including the dead ones it exists for.
    NULL means the rule ages the link from ``created_at``, which is a fact we do have.
    """
    from core.migration_steps_channel_activity import (  # noqa: PLC0415
        _add_campaign_channel_last_post,
    )

    engine = legacy_engine("legacy-last-post.db")
    with engine.begin() as connection:
        _legacy_links(connection)
        _add_campaign_channel_last_post(connection)
        _add_campaign_channel_last_post(connection)  # idempotent — must not raise.

    with engine.connect() as connection:
        last_post_at = connection.exec_driver_sql(
            "SELECT last_post_at FROM neurocomment_campaign_channels",
        ).scalar_one()
    assert last_post_at is None


def test_a_database_without_the_table_is_left_alone(legacy_engine: _EngineFactory) -> None:
    """Runs before the neurocomment tables exist on a fresh DB, so it must no-op there."""
    from core.migration_steps_channel_activity import (  # noqa: PLC0415
        _add_campaign_channel_last_post,
    )

    engine = legacy_engine("legacy-no-table.db")
    with engine.begin() as connection:
        _add_campaign_channel_last_post(connection)
