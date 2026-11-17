"""Migration 117 — spam appeals match ``create_all``; additive; idempotent; one open row; delete."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import IntegrityError

from core.db import _get_engine, _metadata, create_account, delete_account
from core.migration_steps_spam_appeals import _add_spam_appeals
from core.migrations import MIGRATIONS
from core.repositories.spam_appeals import (
    close_open_appeal,
    fetch_latest_appeal,
    fetch_open_appeal,
    file_appeal,
    list_open_appeals,
)
from schemas.accounts import AccountCreate

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_AT = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def _shape(engine: Engine, table: str) -> dict[str, object]:
    with engine.connect() as connection:
        columns = connection.exec_driver_sql(f"PRAGMA table_info({table})").mappings().all()
        indexes = connection.exec_driver_sql(f"PRAGMA index_list({table})").mappings().all()
        partial = {
            r["name"]: connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?", (r["name"],)
            ).scalar()
            for r in indexes
            if not r["name"].startswith("sqlite_")
        }
    return {
        "columns": {
            r["name"]: (r["type"], r["notnull"], r["dflt_value"], r["pk"]) for r in columns
        },
        "indexes": sorted(
            (r["name"], r["unique"], r["partial"])
            for r in indexes
            if not r["name"].startswith("sqlite_")
        ),
        # The partial index's WHERE clause is part of its meaning: normalise the spelling.
        "where": {name: "closed_at IS NULL" in (sql or "") for name, sql in partial.items()},
    }


def test_the_registry_carries_the_migration_exactly_once_after_116() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_spam_appeals]
    assert entries == [(117, "add_spam_appeals", _add_spam_appeals)]
    versions = [version for version, _name, _body in MIGRATIONS]
    assert len(versions) == len(set(versions))
    assert versions == sorted(versions)
    assert versions.index(117) == versions.index(116) + 1


def test_created_and_migrated_schemas_match(legacy_engine: _EngineFactory) -> None:
    created = legacy_engine("created.db")
    _metadata.create_all(created)
    migrated = legacy_engine("migrated.db")
    for _ in range(2):  # idempotent
        with migrated.begin() as connection:
            _add_spam_appeals(connection)
    assert _shape(migrated, "spam_appeals") == _shape(created, "spam_appeals")
    assert _shape(created, "spam_appeals")["where"] == {
        "ix_spam_appeals_account": False,
        "ux_spam_appeals_open": True,
    }


def test_the_migrated_table_keeps_one_open_row_per_account(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("one_open.db")
    with engine.begin() as connection:
        _add_spam_appeals(connection)
        insert = (
            "INSERT INTO spam_appeals (account_id, filed_at, filed_by, verdict, closed_at)"
            " VALUES ('a1', 't', 'op', 'limited', ?)"
        )
        connection.exec_driver_sql(insert, (None,))
        connection.exec_driver_sql(insert, ("later",))  # a closed one beside it is fine
        connection.exec_driver_sql(insert, ("later too",))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.exec_driver_sql(insert, (None,))


@pytest.mark.asyncio
async def test_filing_twice_keeps_the_first_row_and_a_close_lets_a_new_one_in() -> None:
    await create_account(AccountCreate(account_id="a1"))

    first, created = await file_appeal(
        "a1", filed_at=_AT, filed_by="anna", verdict="limited", until_day=date(2026, 11, 20)
    )
    again, created_again = await file_appeal(
        "a1", filed_at=_AT + timedelta(hours=1), filed_by="boris", verdict="unknown", until_day=None
    )

    assert (created, created_again) == (True, False)
    assert again == first
    assert (first.filed_by, first.verdict, first.until_day) == (
        "anna",
        "limited",
        date(2026, 11, 20),
    )
    assert [row.id for row in await list_open_appeals()] == [first.id]

    assert await close_open_appeal("a1", closed_at=_AT + timedelta(days=1), kind="spambot_clean")
    assert not await close_open_appeal("a1", closed_at=_AT, kind="spambot_clean")
    assert await fetch_open_appeal("a1") is None
    closed = await fetch_latest_appeal("a1")
    assert closed is not None
    assert (closed.closed_by_kind, closed.closed_at) == ("spambot_clean", _AT + timedelta(days=1))

    second, created_second = await file_appeal(
        "a1", filed_at=_AT + timedelta(days=2), filed_by="boris", verdict="limited", until_day=None
    )
    assert created_second
    assert second.id != first.id
    assert (await fetch_latest_appeal("a1")) == second


@pytest.mark.asyncio
async def test_deleting_an_account_removes_its_appeals() -> None:
    for account in ("gone", "stays"):
        await create_account(AccountCreate(account_id=account))
        await file_appeal(account, filed_at=_AT, filed_by="op", verdict="limited", until_day=None)
    await close_open_appeal("gone", closed_at=_AT, kind="spambot_clean")
    await file_appeal("gone", filed_at=_AT, filed_by="op", verdict="limited", until_day=None)

    await delete_account("gone")

    assert await fetch_latest_appeal("gone") is None
    with _get_engine().connect() as connection:
        left = connection.exec_driver_sql("SELECT account_id FROM spam_appeals").all()
    assert [row[0] for row in left] == ["stays"]
