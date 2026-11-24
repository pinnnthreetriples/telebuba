"""Migration 124 — ``accounts.link_status`` / ``link_checked_at``: additive and idempotent."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import inspect

from core.db import _metadata
from core.migration_steps_link_status import _add_account_link_status
from core.migrations import MIGRATIONS

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]


def test_the_registry_carries_the_migration() -> None:
    assert (124, "add_account_link_status", _add_account_link_status) in MIGRATIONS


def test_it_adds_both_columns_once(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("legacy.db")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE accounts (account_id VARCHAR PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO accounts VALUES ('a')")
        _add_account_link_status(connection)
        _add_account_link_status(connection)
        row = connection.exec_driver_sql("SELECT link_status, link_checked_at FROM accounts").one()
    assert tuple(row) == (None, None)
    assert {"link_status", "link_checked_at"} <= {
        column["name"] for column in inspect(engine).get_columns("accounts")
    }
    assert "link_status" in _metadata.tables["accounts"].c
