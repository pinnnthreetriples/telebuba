"""Migration 56 — the observed-chat log, and its parity with ``create_all``.

Same two-sided problem migration 55 has: ``core.db._build_engine`` runs
``create_all`` BEFORE ``apply_migrations``, so a FRESH database is built by
``core.repositories.neuroshilling._tables`` and the migration is a no-op on it,
while an EXISTING database is built by the migration and never sees the table
object. Both spellings are load-bearing and neither is exercised by the other's
users, so a column or an index present in only one would silently exist on half
the fleet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import core.repositories.neuroshilling._tables  # noqa: F401 - registers the tables
from core.db import _metadata
from core.migration_steps_neuroshilling import _add_neuroshilling_tables
from core.migration_steps_neuroshilling_chat import _add_neuroshilling_chat_log
from core.migrations import MIGRATIONS
from tests.core.test_migrations_neuroshilling import _table_shape

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_TABLE = "neuroshilling_chat_log"


@pytest.fixture
def created_engine(legacy_engine: _EngineFactory) -> Engine:
    """A database built the way a FRESH install is: ``create_all`` and nothing else."""
    engine = legacy_engine("chat-created.db")
    _metadata.create_all(engine)
    return engine


@pytest.fixture
def migrated_engine(legacy_engine: _EngineFactory) -> Engine:
    """A database built the way an EXISTING install gains the table: the migrations.

    Fifty-five first, because the chat log's foreign key names a table that one
    creates — SQLite accepts the reference either way, but ``PRAGMA
    foreign_key_list`` is only comparable once both ends exist.
    """
    engine = legacy_engine("chat-migrated.db")
    with engine.begin() as connection:
        _add_neuroshilling_tables(connection)
        _add_neuroshilling_chat_log(connection)
    return engine


def test_the_registry_carries_the_migration_exactly_once() -> None:
    """Pinned by identity at 56, the actual maximum of the registry plus one.

    Asserting it is the HIGHEST version would only hand a red test to whoever adds
    57, for a reason that has nothing to do with their change.
    """
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_neuroshilling_chat_log]

    assert entries == [(56, "add_neuroshilling_chat_log", _add_neuroshilling_chat_log)]


def test_created_and_migrated_schemas_match(
    created_engine: Engine,
    migrated_engine: Engine,
) -> None:
    assert _table_shape(created_engine, _TABLE) == _table_shape(migrated_engine, _TABLE)


def test_the_migration_is_idempotent(migrated_engine: Engine) -> None:
    """Re-running it changes nothing — which is what a fresh install really does."""
    before = _table_shape(migrated_engine, _TABLE)
    with migrated_engine.begin() as connection:
        _add_neuroshilling_chat_log(connection)

    assert _table_shape(migrated_engine, _TABLE) == before


def test_one_message_of_one_target_can_only_be_recorded_once(created_engine: Engine) -> None:
    """``ux_ns_chat_log_msg`` is what makes an overlapping re-poll idempotent.

    Two polls seeing the same message is the normal case, not the exceptional one:
    the cursor is inclusive of nothing and Telegram is free to hand back a page
    that overlaps the previous one. The uniqueness is also the cursor itself —
    ``MAX(message_id)`` for the pair is a prefix scan of this index.
    """
    with created_engine.connect() as connection:
        info = connection.exec_driver_sql("PRAGMA index_info(ux_ns_chat_log_msg)").mappings().all()
        listed = connection.exec_driver_sql(f"PRAGMA index_list({_TABLE})").mappings().all()

    assert [row["name"] for row in info] == ["campaign_id", "target", "message_id"]
    assert {row["name"]: row["unique"] for row in listed}["ux_ns_chat_log_msg"] == 1
