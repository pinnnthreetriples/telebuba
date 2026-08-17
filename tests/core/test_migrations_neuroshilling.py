"""Migration 55 — the neuroshilling schema, and its parity with ``create_all``.

``core.db._build_engine`` runs ``create_all`` BEFORE ``apply_migrations``, so a
FRESH database is built by ``core.repositories.neuroshilling._tables`` and the
migration is a no-op on it, while an EXISTING database is built by the migration
and never sees the table objects. The two spellings are therefore both load-
bearing and neither one is exercised by the other's users. A constraint present
in only one of them would silently exist on half the fleet.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

import core.repositories.neuroshilling._tables  # noqa: F401 - registers the tables
from core.db import _metadata, configure_database
from core.migration_steps_neuroshilling import _add_neuroshilling_tables
from core.migrations import MIGRATIONS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_TABLES = (
    "neuroshilling_campaigns",
    "neuroshilling_roles",
    "neuroshilling_accounts",
    "neuroshilling_steps",
    "neuroshilling_presence",
    "neuroshilling_messages",
)


def _check_constraints(create_sql: str) -> set[str]:
    """Every ``CHECK (...)`` clause of a CREATE TABLE, whitespace-normalised.

    PRAGMA exposes columns, foreign keys and indexes but NOT check constraints,
    so this is the only way to compare them. Balanced-paren scanning rather than
    a regex because the bodies contain parenthesised ``IN`` lists.
    """
    found: set[str] = set()
    for match in re.finditer(r"CHECK\s*\(", create_sql, re.IGNORECASE):
        start = match.end() - 1
        depth = 0
        for index in range(start, len(create_sql)):
            if create_sql[index] == "(":
                depth += 1
            elif create_sql[index] == ")":
                depth -= 1
                if depth == 0:
                    found.add(re.sub(r"\s+", " ", create_sql[start : index + 1]).strip())
                    break
    return found


def _checks_of(engine: Engine, table: str) -> set[str]:
    with engine.connect() as connection:
        create_sql = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).scalar_one()
    return _check_constraints(str(create_sql))


def _table_shape(engine: Engine, table: str) -> dict[str, object]:
    with engine.connect() as connection:
        columns = connection.exec_driver_sql(f"PRAGMA table_info({table})").mappings().all()
        keys = connection.exec_driver_sql(f"PRAGMA foreign_key_list({table})").mappings().all()
        index_rows = connection.exec_driver_sql(f"PRAGMA index_list({table})").mappings().all()
        indexes = {}
        for row in index_rows:
            info = (
                connection.exec_driver_sql(
                    f"PRAGMA index_info({row['name']})",
                )
                .mappings()
                .all()
            )
            indexes[row["name"]] = (
                row["unique"],
                row["origin"],
                tuple(entry["name"] for entry in info),
            )
    return {
        "columns": {
            row["name"]: (row["type"], row["notnull"], row["dflt_value"], row["pk"])
            for row in columns
        },
        "foreign_keys": sorted(
            (row["table"], row["from"], row["to"], row["on_update"], row["on_delete"])
            for row in keys
        ),
        "indexes": indexes,
        "checks": sorted(_checks_of(engine, table)),
    }


@pytest.fixture
def created_engine(legacy_engine: _EngineFactory) -> Engine:
    """A database built the way a FRESH install is: ``create_all`` and nothing else."""
    engine = legacy_engine("created.db")
    _metadata.create_all(engine)
    return engine


@pytest.fixture
def migrated_engine(legacy_engine: _EngineFactory) -> Engine:
    """A database built the way an EXISTING install gains the domain: the migration."""
    engine = legacy_engine("migrated.db")
    with engine.begin() as connection:
        _add_neuroshilling_tables(connection)
    return engine


def test_the_registry_carries_the_migration_exactly_once() -> None:
    """Pinned by identity, not by being newest.

    ``version`` is append-only and must never be reused, so what matters is that
    this step appears once, at 55, under its own name and bound to its own body.
    Asserting it is the highest version in the registry would only mean that
    whoever adds migration 56 gets a red test with nothing to do with their change.
    """
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_neuroshilling_tables]

    assert entries == [(55, "add_neuroshilling_tables", _add_neuroshilling_tables)]


@pytest.mark.parametrize("table", _TABLES)
def test_created_and_migrated_schemas_match(
    created_engine: Engine,
    migrated_engine: Engine,
    table: str,
) -> None:
    assert _table_shape(created_engine, table) == _table_shape(migrated_engine, table)


def test_the_migration_is_idempotent(migrated_engine: Engine) -> None:
    """Re-running it on a database that already has the tables changes nothing.

    That is not a hypothetical: on a fresh install ``create_all`` has already
    built every one of these before the registry stamps version 55.
    """
    before = {table: _table_shape(migrated_engine, table) for table in _TABLES}
    with migrated_engine.begin() as connection:
        _add_neuroshilling_tables(connection)
    assert {table: _table_shape(migrated_engine, table) for table in _TABLES} == before


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ("neuroshilling_campaigns", "status IN ('idle','running','stopping','done','failed')"),
        ("neuroshilling_accounts", "state IN ('active','banned','replaced')"),
        ("neuroshilling_steps", "kind IN ('message','reaction')"),
        ("neuroshilling_messages", "status IN ('pending','sent','failed','skipped')"),
    ],
)
def test_the_state_vocabularies_are_enforced_by_the_database(
    created_engine: Engine,
    table: str,
    expected: str,
) -> None:
    """The engine's state machines are constrained in the schema, not only in code."""
    assert f"({expected})" in _checks_of(created_engine, table)


def test_the_journal_cannot_hold_two_rows_for_one_step_of_one_run(tmp_path: Path) -> None:
    """``ux_ns_messages_step`` is what makes "write the row BEFORE sending" a guard.

    Keyed on (run_id, target, step_id) and not on the step alone: the same step
    of the same campaign is legitimately played once per target.
    """
    configure_database(tmp_path / "telebuba.db")
    from core.db import _get_engine  # noqa: PLC0415 - must follow configure_database

    with _get_engine().connect() as connection:
        info = (
            connection.exec_driver_sql(
                "PRAGMA index_info(ux_ns_messages_step)",
            )
            .mappings()
            .all()
        )
        listed = (
            connection.exec_driver_sql(
                "PRAGMA index_list(neuroshilling_messages)",
            )
            .mappings()
            .all()
        )
    assert [row["name"] for row in info] == ["run_id", "target", "step_id"]
    assert {row["name"]: row["unique"] for row in listed}["ux_ns_messages_step"] == 1
