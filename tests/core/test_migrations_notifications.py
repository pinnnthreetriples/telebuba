"""Migration 81 — the operator-bot settings table matches ``create_all``; the repository."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import _metadata
from core.migration_steps_notifications import _add_notification_settings
from core.migrations import MIGRATIONS
from core.repositories.notification_settings import (
    load_notification_settings,
    save_notification_settings,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_TABLE = "notification_settings"
_TOKEN = "123456:AAH-secret_token_value_abcdefXYZ"


def _shape(engine: Engine) -> dict[str, object]:
    with engine.connect() as connection:
        columns = connection.exec_driver_sql(f"PRAGMA table_info({_TABLE})").mappings().all()
    return {r["name"]: (r["type"], r["notnull"], r["dflt_value"], r["pk"]) for r in columns}


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_notification_settings]
    assert entries == [(81, "add_notification_settings", _add_notification_settings)]


def test_created_and_migrated_schemas_match(legacy_engine: _EngineFactory) -> None:
    created = legacy_engine("created.db")
    _metadata.create_all(created)
    migrated = legacy_engine("migrated.db")
    for _ in range(2):  # idempotent
        with migrated.begin() as connection:
            _add_notification_settings(connection)
    assert _shape(created) == _shape(migrated)


@pytest.mark.asyncio
async def test_nothing_saved_reads_as_off_without_a_token() -> None:
    stored = await load_notification_settings()
    assert (stored.enabled, stored.bot_token, stored.chat_id) == (False, "", "")
    assert stored.updated_at is None


@pytest.mark.asyncio
async def test_save_keeps_clears_and_replaces() -> None:
    first = await save_notification_settings(enabled=True, bot_token=_TOKEN, chat_id="42")
    assert (first.enabled, first.bot_token, first.chat_id) == (True, _TOKEN, "42")

    kept = await save_notification_settings(enabled=None, bot_token=None, chat_id=None)
    assert (kept.enabled, kept.bot_token, kept.chat_id) == (True, _TOKEN, "42")

    cleared = await save_notification_settings(enabled=False, bot_token="", chat_id="@ops_room")
    assert (cleared.enabled, cleared.bot_token, cleared.chat_id) == (False, "", "@ops_room")
    assert cleared.updated_at is not None
