"""Migration 119 — ``primary_observed_at`` matches ``create_all``; additive; idempotent."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from core.db import _metadata, create_account
from core.migration_steps_spam_projection_observed import _add_spam_projection_observed
from core.migrations import MIGRATIONS
from core.repositories.spam_status import list_projections_by_ids, store_projection
from schemas.accounts import AccountCreate
from schemas.spam_evidence import Verdict

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

# ``account_spam_status`` as migration 116 left it (before the observation instant).
_OLD_STATUS = (
    "CREATE TABLE account_spam_status ("
    " account_id VARCHAR NOT NULL, status VARCHAR NOT NULL, detail VARCHAR,"
    " checked_at VARCHAR NOT NULL, confidence VARCHAR, valid_until VARCHAR,"
    " primary_kind VARCHAR, last_spambot_at VARCHAR, needs_probe INTEGER,"
    " fold_status VARCHAR, fold_reason VARCHAR, tos_restricted INTEGER,"
    " tos_reason VARCHAR, folded_at VARCHAR, PRIMARY KEY (account_id))"
)


def _columns(engine: Engine) -> dict[str, tuple[object, ...]]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql("PRAGMA table_info(account_spam_status)").mappings().all()
    return {r["name"]: (r["type"], r["notnull"], r["dflt_value"], r["pk"]) for r in rows}


def test_the_registry_carries_the_migration_once_and_in_order() -> None:
    entries = [e for e in MIGRATIONS if e[2] is _add_spam_projection_observed]
    assert entries == [(119, "add_spam_projection_observed", _add_spam_projection_observed)]
    versions = [version for version, _name, _body in MIGRATIONS]
    assert len(versions) == len(set(versions))
    assert versions == sorted(versions)


def test_created_and_migrated_schemas_match(legacy_engine: _EngineFactory) -> None:
    created = legacy_engine("created.db")
    _metadata.create_all(created)
    migrated = legacy_engine("migrated.db")
    with migrated.begin() as connection:
        connection.exec_driver_sql(_OLD_STATUS)
    for _ in range(2):  # idempotent
        with migrated.begin() as connection:
            _add_spam_projection_observed(connection)

    assert _columns(migrated) == _columns(created)
    assert "primary_observed_at" in _columns(migrated)


def test_old_rows_read_none_and_are_otherwise_untouched(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("old.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(_OLD_STATUS)
        connection.exec_driver_sql(
            "INSERT INTO account_spam_status (account_id, status, checked_at, fold_status)"
            " VALUES ('a1', 'limited', '2026-07-01T00:00:00+00:00', 'limited')"
        )
        _add_spam_projection_observed(connection)
    with engine.connect() as connection:
        row = connection.exec_driver_sql("SELECT * FROM account_spam_status").mappings().one()
    assert (row["status"], row["fold_status"]) == ("limited", "limited")
    assert row["primary_observed_at"] is None


def test_a_database_without_the_table_is_left_alone(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("bare.db")
    with engine.begin() as connection:
        _add_spam_projection_observed(connection)
    assert _columns(engine) == {}


@pytest.mark.asyncio
async def test_the_projection_round_trips_the_observation_instant() -> None:
    seen = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    await create_account(AccountCreate(account_id="a1"))
    verdict = Verdict(status="limited", primary_kind="peer_flood_send", primary_observed_at=seen)
    await store_projection("a1", verdict, seen)

    stored = (await list_projections_by_ids(["a1"]))["a1"].verdict
    assert stored is not None
    assert (stored.primary_kind, stored.primary_observed_at) == ("peer_flood_send", seen)
