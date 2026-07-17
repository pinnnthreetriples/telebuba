"""State-writer behavior: carry, clear, CAS, and deleted-account cards."""

from __future__ import annotations

import pytest

from core.db import create_account, fetch_warming_state, upsert_warming_state
from schemas.accounts import AccountCreate
from schemas.warming import WarmingStateWrite
from services.warming._state import _current_card, _set_state


@pytest.mark.asyncio
async def test_unspecified_fields_carry_while_explicit_none_clears() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A"))
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            last_event="old",
            last_error="failure",
            last_action="react",
            last_channel="@old",
            next_run_at="2026-07-18T12:00:00+00:00",
            flood_wait_seconds=60,
            run_id="generation-a",
            daily_actions=7,
            quarantine_count=2,
        )
    )

    write = await _set_state(
        "acc-1",
        "active",
        last_event="resumed",
        last_error=None,
        last_channel=None,
        flood_wait_seconds=None,
    )

    assert write.applied is True
    record = write.record
    assert record.state == "active"
    assert record.last_event == "resumed"
    assert record.last_error is None
    assert record.last_channel is None
    assert record.flood_wait_seconds is None
    assert record.last_action == "react"
    assert record.next_run_at == "2026-07-18T12:00:00+00:00"
    assert record.run_id == "generation-a"
    assert record.daily_actions == 7
    assert record.quarantine_count == 2


@pytest.mark.asyncio
async def test_cas_rejection_preserves_new_generation() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1", state="active", run_id="generation-b", last_event="queued"
        )
    )

    write = await _set_state(
        "acc-1",
        "sleeping",
        last_event="stale-cycle",
        expected_run_id="generation-a",
    )

    assert write.applied is False
    assert write.record.state == "active"
    assert write.record.run_id == "generation-b"
    assert write.record.last_event == "queued"


@pytest.mark.asyncio
async def test_first_increment_starts_at_one_and_followup_is_atomic() -> None:
    await create_account(AccountCreate(account_id="acc-1"))

    first = await _set_state("acc-1", "sleeping", increment_cycle=True)
    second = await _set_state("acc-1", "sleeping", increment_cycle=True)

    assert first.record.cycles_completed == 1
    assert second.record.cycles_completed == 2


@pytest.mark.asyncio
async def test_current_card_for_deleted_account_preserves_record_contract() -> None:
    # Orphan rows can exist after interrupted migrations/reconciliation. Rendering
    # one must keep operator-visible progress rather than fabricating defaults.
    await create_account(AccountCreate(account_id="orphan"))
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="orphan",
            state="quarantine",
            cycles_completed=4,
            last_event="quarantine_extended",
            quarantine_count=2,
            target_days=14,
            activity_persona="active",
        )
    )
    from core.db import _accounts, _get_engine  # noqa: PLC0415

    with _get_engine().begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(_accounts.delete().where(_accounts.c.account_id == "orphan"))

    card = await _current_card("orphan")

    assert card.account_id == "orphan"
    assert card.label == "orphan"
    assert card.state == "quarantine"
    assert card.cycles_completed == 4
    assert card.quarantine_count == 2
    assert card.target_days == 14
    assert card.activity_persona == "active"
    assert await fetch_warming_state("orphan") is not None
