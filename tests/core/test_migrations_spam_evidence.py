"""Migration 116 — spam evidence matches ``create_all``; additive; idempotent; account delete."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.db import _get_engine, _metadata, create_account, delete_account
from core.migration_steps_spam_evidence import _add_spam_evidence
from core.migrations import MIGRATIONS
from core.repositories.spam_evidence import list_evidence, record_evidence
from core.repositories.spam_status import store_projection
from schemas.accounts import AccountCreate
from schemas.spam_evidence import Evidence, Verdict

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_TABLES = ("account_spam_evidence", "account_spam_status")
# ``account_spam_status`` as it was before 116 (the three columns the old readers use).
_OLD_STATUS = (
    "CREATE TABLE account_spam_status ("
    " account_id VARCHAR NOT NULL, status VARCHAR NOT NULL, detail VARCHAR,"
    " checked_at VARCHAR NOT NULL, PRIMARY KEY (account_id))"
)
_NEW_COLUMNS = {
    "confidence",
    "valid_until",
    "primary_kind",
    "last_spambot_at",
    "needs_probe",
    "fold_status",
    "fold_reason",
    "tos_restricted",
    "tos_reason",
    "folded_at",
}


def _shape(engine: Engine, table: str) -> dict[str, object]:
    with engine.connect() as connection:
        columns = connection.exec_driver_sql(f"PRAGMA table_info({table})").mappings().all()
        indexes = connection.exec_driver_sql(f"PRAGMA index_list({table})").mappings().all()
    return {
        "columns": {
            r["name"]: (r["type"], r["notnull"], r["dflt_value"], r["pk"]) for r in columns
        },
        "indexes": sorted(r["name"] for r in indexes if not r["name"].startswith("sqlite_")),
    }


def _previous_version(legacy_engine: _EngineFactory, name: str) -> Engine:
    """A pre-116 database: the old status table, no evidence table."""
    engine = legacy_engine(name)
    with engine.begin() as connection:
        connection.exec_driver_sql(_OLD_STATUS)
    return engine


def test_the_registry_carries_the_migration_exactly_once_after_115() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_spam_evidence]
    assert entries == [(116, "add_spam_evidence", _add_spam_evidence)]
    versions = [version for version, _name, _body in MIGRATIONS]
    assert len(versions) == len(set(versions))
    assert versions == sorted(versions)


@pytest.mark.parametrize("table", _TABLES)
def test_created_and_migrated_schemas_match(legacy_engine: _EngineFactory, table: str) -> None:
    created = legacy_engine("created.db")
    _metadata.create_all(created)
    migrated = _previous_version(legacy_engine, "migrated.db")
    for _ in range(2):  # idempotent
        with migrated.begin() as connection:
            _add_spam_evidence(connection)
    # Columns, types, nullability, defaults, primary key and index names; the foreign key
    # on ``account_spam_status.account_id`` is not part of the shape.
    assert _shape(migrated, table) == _shape(created, table)


def test_the_projection_columns_are_additive_and_nullable(legacy_engine: _EngineFactory) -> None:
    engine = _previous_version(legacy_engine, "old.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO account_spam_status VALUES ('a1', 'limited', 'until 29 Oct', 't1')"
        )
        _add_spam_evidence(connection)
    with engine.connect() as connection:
        row = connection.exec_driver_sql("SELECT * FROM account_spam_status").mappings().one()
    assert (row["status"], row["detail"], row["checked_at"]) == ("limited", "until 29 Oct", "t1")
    assert {name for name in row if name in _NEW_COLUMNS and row[name] is None} == _NEW_COLUMNS


def test_a_database_without_a_status_table_still_gets_the_evidence_table(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("bare.db")
    with engine.begin() as connection:
        _add_spam_evidence(connection)
    assert _shape(engine, "account_spam_evidence")["columns"]


def _item(account: str, at: datetime) -> Evidence:
    return Evidence(
        account_id=account,
        kind="peer_flood_send",
        direction="limited",
        observed_at=at,
        source="test",
    )


@pytest.mark.asyncio
async def test_deleting_an_account_removes_its_evidence_and_projection() -> None:
    now = datetime.now(UTC)
    window = timedelta(0)
    await create_account(AccountCreate(account_id="gone"))
    await create_account(AccountCreate(account_id="stays"))
    for account in ("gone", "stays"):
        await record_evidence(_item(account, now), window=window)
        await store_projection(account, Verdict(status="limited"), now)

    await delete_account("gone")

    assert await list_evidence("gone") == []
    assert len(await list_evidence("stays")) == 1
    with _get_engine().connect() as connection:
        left = connection.exec_driver_sql("SELECT account_id FROM account_spam_status").all()
    assert [row[0] for row in left] == ["stays"]
