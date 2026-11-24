"""The key-intent journal: its rows, migration 122 and its parity with ``create_all``."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.db import _metadata, configure_database, create_account
from core.migration_steps_key_intents import _add_account_key_intents
from core.migrations import MIGRATIONS
from core.repositories.account_key_intents import (
    advance_intent,
    close_intent,
    fetch_intent,
    list_open_intents,
    open_intent,
)
from core.repositories.accounts import _delete_account
from schemas.accounts import AccountCreate
from tests.core.test_migrations_scheduled_posts import _shape

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]


@pytest.fixture
def created_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("created.db")
    _metadata.create_all(engine)
    return engine


@pytest.fixture
def migrated_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("migrated.db")
    with engine.begin() as connection:
        _add_account_key_intents(connection)
    return engine


def test_the_registry_carries_the_migration_exactly_once_after_121() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_account_key_intents]
    assert entries == [(122, "add_account_key_intents", _add_account_key_intents)]


def test_created_and_migrated_tables_match(created_engine: Engine, migrated_engine: Engine) -> None:
    assert _shape(created_engine, "account_key_intents") == _shape(
        migrated_engine, "account_key_intents"
    )


def test_the_migration_is_idempotent(migrated_engine: Engine) -> None:
    before = _shape(migrated_engine, "account_key_intents")
    with migrated_engine.begin() as connection:
        _add_account_key_intents(connection)
    assert _shape(migrated_engine, "account_key_intents") == before


@pytest.fixture
def db(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


@pytest.mark.asyncio
@pytest.mark.usefixtures("db")
async def test_an_intent_is_opened_advanced_and_closed() -> None:
    intent_id = await open_intent(
        account_id="acc-a",
        kind="rekey",
        device_type="desktop",
        session_name="acc-a_rekey",
        api_id=7,
        old_key_mark="ab" * 32,
    )

    opened = await fetch_intent(intent_id)
    assert opened is not None
    assert (opened.stage, opened.api_id, opened.old_key_mark) == ("started", 7, "ab" * 32)
    assert (opened.closed_at, opened.outcome, opened.new_hash) == (None, None, None)
    assert [i.id for i in await list_open_intents("acc-a")] == [intent_id]

    await advance_intent(intent_id, stage="signed_in", new_hash="222")
    await advance_intent(intent_id, old_hash="111")
    await advance_intent(intent_id)
    moved = await fetch_intent(intent_id)
    assert moved is not None
    assert (moved.stage, moved.new_hash, moved.old_hash) == ("signed_in", "222", "111")

    await close_intent(intent_id, "undone")
    await close_intent(intent_id, "done")
    closed = await fetch_intent(intent_id)
    assert closed is not None
    assert (closed.outcome, closed.closed_at is not None) == ("undone", True)
    assert await list_open_intents() == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("db")
async def test_open_intents_are_listed_oldest_first_and_per_account() -> None:
    first = await open_intent(
        account_id="acc-a", kind="sign_in", device_type="desktop", session_name="a"
    )
    other = await open_intent(
        account_id="acc-b", kind="second_device", device_type="mobile", session_name="b"
    )

    assert [i.id for i in await list_open_intents()] == [first, other]
    assert [i.id for i in await list_open_intents("acc-b")] == [other]
    assert await fetch_intent(other + 99) is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("db")
async def test_an_intent_goes_with_its_account() -> None:
    await create_account(AccountCreate(account_id="acc-a"))
    await create_account(AccountCreate(account_id="acc-b"))
    gone = await open_intent(
        account_id="acc-a", kind="rekey", device_type="desktop", session_name="a_rekey"
    )
    kept = await open_intent(
        account_id="acc-b", kind="rekey", device_type="desktop", session_name="b_rekey"
    )

    await asyncio.to_thread(_delete_account, "acc-a")

    assert await fetch_intent(gone) is None
    assert await fetch_intent(kept) is not None
