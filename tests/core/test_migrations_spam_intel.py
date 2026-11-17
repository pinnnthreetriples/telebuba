"""Migration 114 — the spam-intel ledger matches ``create_all``; seed; repositories; delete."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from core.db import _get_engine, _metadata, create_account, delete_account
from core.migration_steps_spam_intel import _add_spam_probe_ledger
from core.migrations import MIGRATIONS
from core.repositories.spam_probe_ledger import (
    delete_account_ledger,
    fetch_probe_state,
    finish_attempt,
    load_budget_snapshot,
    prune_attempts,
    save_probe_state,
    start_attempt,
)
from core.repositories.spam_reply_samples import list_reply_samples, upsert_reply_sample
from schemas.accounts import AccountCreate
from schemas.spam_intel import ProbeAttemptFinish, ProbeAttemptStart, ProbeState, ReplySample

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_TABLES = ("spam_probe_attempts", "spam_probe_state", "spam_reply_samples")


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


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_spam_probe_ledger]
    assert entries == [(114, "add_spam_probe_ledger", _add_spam_probe_ledger)]
    versions = [version for version, _name, _body in MIGRATIONS]
    assert len(versions) == len(set(versions))  # no number is used twice


@pytest.mark.parametrize("table", _TABLES)
def test_created_and_migrated_schemas_match(legacy_engine: _EngineFactory, table: str) -> None:
    created = legacy_engine("created.db")
    _metadata.create_all(created)
    migrated = legacy_engine("migrated.db")
    for _ in range(2):  # idempotent
        with migrated.begin() as connection:
            _add_spam_probe_ledger(connection)
    assert _shape(created, table) == _shape(migrated, table)


def _legacy_with_journal(legacy_engine: _EngineFactory) -> Engine:
    """A pre-114 database: an accounts table and a lifecycle journal, no ledger."""
    engine = legacy_engine("legacy.db")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE accounts (account_id VARCHAR, proxy_id VARCHAR)")
        connection.exec_driver_sql(
            "CREATE TABLE account_lifecycle_events (id INTEGER PRIMARY KEY, account_id VARCHAR,"
            " kind VARCHAR, created_at VARCHAR, extra VARCHAR)"
        )
        connection.exec_driver_sql("INSERT INTO accounts VALUES ('a1', 'px1'), ('a2', NULL)")
        now = datetime.now(UTC)
        rows = [
            ("a1", "spambot_verdict", now - timedelta(days=2), '{"status": "clean"}'),
            ("a1", "spambot_verdict", now - timedelta(days=20), '{"status": "limited"}'),
            ("a2", "spambot_verdict", now - timedelta(hours=3), '{"status": "limited"}'),
            ("a2", "warmed", now - timedelta(hours=1), "{}"),
            ("a2", "spambot_verdict", now - timedelta(hours=2), "{}"),
        ]
        for account, kind, at, extra in rows:
            connection.exec_driver_sql(
                "INSERT INTO account_lifecycle_events (account_id, kind, created_at, extra)"
                " VALUES (?, ?, ?, ?)",
                (account, kind, at.isoformat(), extra),
            )
    return engine


def test_the_ledger_is_seeded_from_the_last_fourteen_days_of_answers(
    legacy_engine: _EngineFactory,
) -> None:
    engine = _legacy_with_journal(legacy_engine)
    with engine.begin() as connection:
        _add_spam_probe_ledger(connection)
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT account_id, proxy_key, kind, caller, outcome FROM spam_probe_attempts"
            " ORDER BY started_at"
        ).all()
    assert [tuple(r) for r in rows] == [
        ("a1", "px1", "probe", "backfill", "clean"),
        ("a2", None, "probe", "backfill", "limited"),
        ("a2", None, "probe", "backfill", "unknown"),
    ]


def test_a_second_run_does_not_seed_twice(legacy_engine: _EngineFactory) -> None:
    engine = _legacy_with_journal(legacy_engine)
    for _ in range(3):
        with engine.begin() as connection:
            _add_spam_probe_ledger(connection)
    with engine.connect() as connection:
        count = connection.exec_driver_sql("SELECT COUNT(*) FROM spam_probe_attempts").scalar()
    assert count == 3


def test_a_database_without_a_journal_migrates_clean(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("bare.db")
    with engine.begin() as connection:
        _add_spam_probe_ledger(connection)
    for table in _TABLES:
        assert _shape(engine, table)["columns"]


def _start(account: str, at: datetime, **fields: Any) -> ProbeAttemptStart:
    base: dict[str, Any] = {
        "account_id": account,
        "proxy_key": "px1",
        "purpose": "routine",
        "caller": "test",
        "started_at": at.isoformat(),
    }
    base.update(fields)
    return ProbeAttemptStart.model_validate(base)


@pytest.mark.asyncio
async def test_an_attempt_is_written_when_it_starts_and_closed_when_it_ends() -> None:
    now = datetime.now(UTC)
    attempt_id = await start_attempt(_start("a1", now))
    with _get_engine().connect() as connection:
        started = connection.exec_driver_sql(
            "SELECT outcome, finished_at FROM spam_probe_attempts WHERE id = ?", (attempt_id,)
        ).one()
    assert tuple(started) == ("started", None)
    await finish_attempt(
        ProbeAttemptFinish(
            attempt_id=attempt_id,
            finished_at=now.isoformat(),
            outcome="error",
            error_type="TimeoutError",
            duration_ms=12,
        )
    )
    with _get_engine().connect() as connection:
        done = connection.exec_driver_sql(
            "SELECT outcome, error_type, duration_ms FROM spam_probe_attempts WHERE id = ?",
            (attempt_id,),
        ).one()
    assert tuple(done) == ("error", "TimeoutError", 12)


@pytest.mark.asyncio
async def test_the_budget_snapshot_counts_probes_in_the_window_per_account_proxy_and_purpose() -> (
    None
):
    now = datetime.now(UTC)
    since = (now - timedelta(hours=24)).isoformat()
    await start_attempt(_start("a1", now - timedelta(hours=30)))  # outside the window
    await start_attempt(_start("a1", now - timedelta(hours=10), purpose="operator"))
    await start_attempt(_start("a1", now - timedelta(hours=2)))
    await start_attempt(_start("a2", now - timedelta(hours=1), proxy_key="px2"))
    await start_attempt(_start("a3", now - timedelta(minutes=5), kind="history", proxy_key="px1"))
    snapshot = await load_budget_snapshot("a1", "px1", since)
    assert snapshot.fleet_count_24h == 3  # history reads do not spend the probe caps
    assert snapshot.account_count_24h == 2
    assert snapshot.operator_count_24h == 1
    assert snapshot.fleet_oldest_24h == (now - timedelta(hours=10)).isoformat()
    assert snapshot.account_oldest_24h == (now - timedelta(hours=10)).isoformat()
    assert snapshot.last_attempt_started_at == (now - timedelta(hours=2)).isoformat()
    # Spacing does count the history read, on the proxy and in the fleet.
    assert snapshot.last_proxy_started_at == (now - timedelta(minutes=5)).isoformat()
    assert snapshot.last_fleet_started_at == (now - timedelta(minutes=5)).isoformat()


@pytest.mark.asyncio
async def test_an_empty_ledger_reads_as_a_free_budget() -> None:
    snapshot = await load_budget_snapshot("a1", None, "2000-01-01T00:00:00+00:00")
    assert snapshot.state is None
    assert (snapshot.fleet_count_24h, snapshot.account_count_24h, snapshot.operator_count_24h) == (
        0,
        0,
        0,
    )
    assert snapshot.last_proxy_started_at is None
    assert snapshot.last_fleet_started_at is None


@pytest.mark.asyncio
async def test_the_state_round_trips_and_replaces() -> None:
    assert await fetch_probe_state("a1") is None
    await save_probe_state(ProbeState(account_id="a1", fail_streak=2, last_error="TimeoutError"))
    await save_probe_state(ProbeState(account_id="a1", fail_streak=0, history_read_at="t"))
    state = await fetch_probe_state("a1")
    assert state is not None
    assert (state.fail_streak, state.last_error, state.history_read_at) == (0, None, "t")


@pytest.mark.asyncio
async def test_prune_drops_the_old_and_then_the_oldest_beyond_the_cap() -> None:
    now = datetime.now(UTC)
    for days in (100, 95, 5, 4, 3, 2, 1):
        await start_attempt(_start("a1", now - timedelta(days=days)))
    removed = await prune_attempts((now - timedelta(days=90)).isoformat(), max_rows=3)
    assert removed == 4
    with _get_engine().connect() as connection:
        left = (
            connection.exec_driver_sql(
                "SELECT started_at FROM spam_probe_attempts ORDER BY started_at"
            )
            .scalars()
            .all()
        )
    assert left == [(now - timedelta(days=d)).isoformat() for d in (3, 2, 1)]


@pytest.mark.asyncio
async def test_samples_count_repeat_sightings_and_keep_the_least_recently_seen_out() -> None:
    def sample(key: str, at: str) -> ReplySample:
        return ReplySample(text_hash=key, sample=f"text {key}", first_seen_at=at, last_seen_at=at)

    await upsert_reply_sample(sample("a", "2026-01-01"), max_rows=2)
    await upsert_reply_sample(sample("a", "2026-01-05"), max_rows=2)
    await upsert_reply_sample(sample("b", "2026-01-02"), max_rows=2)
    await upsert_reply_sample(sample("c", "2026-01-03"), max_rows=2)
    kept = {s.text_hash: s.seen_count for s in await list_reply_samples()}
    assert kept == {"a": 2, "c": 1}


@pytest.mark.asyncio
async def test_deleting_an_account_removes_its_attempts_and_state_only() -> None:
    await create_account(AccountCreate(account_id="a1"))
    now = datetime.now(UTC)
    await start_attempt(_start("a1", now))
    await start_attempt(_start("a2", now))
    await save_probe_state(ProbeState(account_id="a1"))
    await save_probe_state(ProbeState(account_id="a2"))
    await delete_account("a1")
    assert await fetch_probe_state("a1") is None
    assert await fetch_probe_state("a2") is not None
    with _get_engine().connect() as connection:
        accounts = (
            connection.exec_driver_sql("SELECT DISTINCT account_id FROM spam_probe_attempts")
            .scalars()
            .all()
        )
    assert accounts == ["a2"]


def test_the_delete_helper_runs_inside_a_transaction() -> None:
    with _get_engine().begin() as connection:
        delete_account_ledger(connection, "nobody")
