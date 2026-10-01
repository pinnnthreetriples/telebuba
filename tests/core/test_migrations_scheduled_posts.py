"""Migration 67 — the scheduled-post schema, and its parity with ``create_all``.

A fresh database is built by ``core.repositories.scheduled_posts._tables`` and an
existing one by the migration; neither path exercises the other, so the two
spellings are compared here down to CHECK constraints, keys and indexes.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

import core.repositories.scheduled_posts._tables  # noqa: F401 - registers the tables
from core.db import _metadata
from core.migration_steps_scheduled_posts import _add_scheduled_posts_tables
from core.migrations import MIGRATIONS

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_TABLES = ("scheduled_profile_posts", "scheduled_post_media")


def _checks(engine: Engine, table: str) -> list[str]:
    with engine.connect() as connection:
        sql = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).scalar_one()
    found = []
    for match in re.finditer(r"CHECK\s*\(", str(sql)):
        depth, start = 0, match.end() - 1
        for index in range(start, len(sql)):
            depth += {"(": 1, ")": -1}.get(sql[index], 0)
            if depth == 0:
                found.append(re.sub(r"\s+", " ", sql[start : index + 1]))
                break
    return sorted(found)


def _shape(engine: Engine, table: str) -> dict[str, object]:
    with engine.connect() as connection:
        run = connection.exec_driver_sql
        columns = run(f"PRAGMA table_info({table})").mappings().all()
        keys = run(f"PRAGMA foreign_key_list({table})").mappings().all()
        indexes = {
            row["name"]: (
                row["unique"],
                row["origin"],
                tuple(i["name"] for i in run(f"PRAGMA index_info({row['name']})").mappings()),
            )
            for row in run(f"PRAGMA index_list({table})").mappings().all()
        }
    return {
        "columns": {
            r["name"]: (r["type"], r["notnull"], r["dflt_value"], r["pk"]) for r in columns
        },
        "keys": sorted((r["table"], r["from"], r["to"], r["on_delete"]) for r in keys),
        "indexes": indexes,
        "checks": _checks(engine, table),
    }


@pytest.fixture
def created_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("created.db")
    _metadata.create_all(engine)
    return engine


@pytest.fixture
def migrated_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("migrated.db")
    _metadata.tables["accounts"].create(engine)
    with engine.begin() as connection:
        _add_scheduled_posts_tables(connection)
    return engine


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_scheduled_posts_tables]
    assert entries == [(67, "add_scheduled_posts_tables", _add_scheduled_posts_tables)]


@pytest.mark.parametrize("table", _TABLES)
def test_created_and_migrated_schemas_match(
    created_engine: Engine,
    migrated_engine: Engine,
    table: str,
) -> None:
    assert _shape(created_engine, table) == _shape(migrated_engine, table)


def test_the_migration_is_idempotent(migrated_engine: Engine) -> None:
    before = {table: _shape(migrated_engine, table) for table in _TABLES}
    with migrated_engine.begin() as connection:
        _add_scheduled_posts_tables(connection)
    assert {table: _shape(migrated_engine, table) for table in _TABLES} == before


def test_the_state_machine_is_enforced_by_the_database(created_engine: Engine) -> None:
    checks = _checks(created_engine, "scheduled_profile_posts")
    assert "(stage IN ('queued','dispatching'))" in checks
    assert any("'ambiguous'" in check for check in checks)
