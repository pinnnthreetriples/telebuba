"""Migration #60: discovery candidates learn ``kind``, and the seen table appears.

Own module because ``test_migrations`` sits at the 700-line test cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import _get_engine, configure_database  # type: ignore[attr-defined]
from core.migration_steps_discovery import (
    _add_discovery_kind_and_seen,
    _add_neurocomment_discovery_candidates,
)
from core.migrations import MIGRATIONS

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Connection

    from tests.core.conftest import _EngineFactory


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


def _columns(connection: Connection, table: str) -> set[str]:
    return {
        str(row["name"])
        for row in connection.exec_driver_sql(f"PRAGMA table_info({table})").mappings()
    }


def test_registered_after_premium_as_sixty() -> None:
    versions = [(version, name) for version, name, _fn in MIGRATIONS if version >= 58]
    assert versions == [
        (58, "add_neurocomment_account_limits"),
        (59, "add_account_premium"),
        (60, "add_discovery_kind_and_seen"),
    ]


def test_kind_column_and_seen_table_added_and_idempotent(legacy_engine: _EngineFactory) -> None:
    """A pre-#60 row reads as a channel: every candidate stored so far was one."""
    engine = legacy_engine("legacy-kind.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE neurocomment_campaigns (campaign_id VARCHAR PRIMARY KEY)",
        )
        _add_neurocomment_discovery_candidates(connection)
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_discovery_candidates "
            "(campaign_id, channel, source, created_at) VALUES ('c1', 'durov', 'x', 't')",
        )
        _add_discovery_kind_and_seen(connection)
        _add_discovery_kind_and_seen(connection)

    with engine.connect() as connection:
        kind = connection.exec_driver_sql(
            "SELECT kind FROM neurocomment_discovery_candidates",
        ).scalar_one()
        seen = _columns(connection, "neurocomment_discovery_seen")
    assert kind == "channel"
    assert seen == {"channel", "first_seen_at", "last_seen_at"}


def test_upgraded_candidates_match_a_fresh_database(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("legacy-parity.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE neurocomment_campaigns (campaign_id VARCHAR PRIMARY KEY)",
        )
        _add_neurocomment_discovery_candidates(connection)
        _add_discovery_kind_and_seen(connection)
        upgraded = _columns(connection, "neurocomment_discovery_candidates")
    with _get_engine().connect() as connection:
        built = _columns(connection, "neurocomment_discovery_candidates")

    assert upgraded == built
    assert "kind" in built
