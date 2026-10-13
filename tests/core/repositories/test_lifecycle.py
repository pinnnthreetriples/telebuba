"""The lifecycle journal repository: snapshot-on-write, round-trip, survives a delete."""

from __future__ import annotations

import pytest

from core.db import _get_engine, create_account, delete_account, upsert_warming_state
from core.repositories.lifecycle import (
    append_lifecycle_event,
    list_all_lifecycle_events,
    list_lifecycle_events,
)
from schemas.accounts import AccountCreate
from schemas.warming import WarmingStateWrite


@pytest.mark.asyncio
async def test_append_round_trips_and_snapshots_the_stint() -> None:
    await create_account(AccountCreate(account_id="a1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="a1", state="active", started_at="2026-10-01T00:00:00+00:00")
    )

    written = await append_lifecycle_event("a1", "spambot_verdict", {"status": "clean"})

    assert written.kind == "spambot_verdict"
    assert written.extra == {"status": "clean"}
    assert written.stint_started_at == "2026-10-01T00:00:00+00:00"
    assert written.backfilled is False
    assert await list_lifecycle_events("a1") == [written]


@pytest.mark.asyncio
async def test_an_unknown_account_gets_no_snapshot() -> None:
    written = await append_lifecycle_event("ghost", "deleted")

    assert written.tg_user_id is None
    assert written.stint_started_at is None
    assert written.extra == {}


@pytest.mark.asyncio
async def test_events_survive_the_account_delete_in_order() -> None:
    await create_account(AccountCreate(account_id="a1"))
    first = await append_lifecycle_event("a1", "warming_started")
    second = await append_lifecycle_event("a1", "deleted", {"reason": "dead"})

    await delete_account("a1")

    assert await list_lifecycle_events("a1") == [first, second]
    assert await list_lifecycle_events("other") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["{broken", "[1, 2]"])
async def test_an_unreadable_extra_reads_back_empty(raw: str) -> None:
    written = await append_lifecycle_event("a1", "warmed")
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE account_lifecycle_events SET extra = ? WHERE id = ?", (raw, written.id)
        )

    [read] = await list_lifecycle_events("a1")

    assert read.extra == {}


@pytest.mark.asyncio
async def test_the_whole_journal_reads_in_one_go_or_for_some_ids() -> None:
    first = await append_lifecycle_event("a1", "warming_started")
    second = await append_lifecycle_event("a2", "warmed")
    third = await append_lifecycle_event("a1", "deleted", {"reason": "retired"})

    assert await list_all_lifecycle_events() == [first, second, third]
    assert await list_all_lifecycle_events(["a1"]) == [first, third]
    assert await list_all_lifecycle_events([]) == []
