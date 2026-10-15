"""One account's wave history: start, end, why it ended, worked days — current life only."""

from __future__ import annotations

import pytest

from core.db import create_account, upsert_warming_state
from core.repositories.lifecycle import append_lifecycle_event
from schemas.accounts import AccountCreate
from schemas.lifecycle import LifecycleSnapshot
from schemas.warming import WarmingStateRecord, WarmingStateWrite
from services.survival import load_account_waves
from services.survival.waves import account_waves
from tests.services.survival._support import iso, row


def _record(**fields: object) -> WarmingStateRecord:
    base: dict[str, object] = {"account_id": "acc-1", "state": "sleeping", "updated_at": "t"}
    return WarmingStateRecord.model_validate({**base, **fields})


def test_every_wave_has_its_start_end_reason_and_worked_days() -> None:
    events = [
        row("warming_started", 0, persona="calm", target_days=14, wave=1),
        row("warmed", 14, warming_days=14),
        row("promoted", 15, wave=1, worked_days=14),
        row("warming_started", 20, stint=20, persona="normal", target_days=7, wave=2),
        row("warming_stopped", 23, stint=20, reason="spam_blocked", wave=2, worked_days=3),
        row("warming_started", 30, stint=30, target_days=10, wave=3),
    ]
    live = _record(started_at=iso(30), warmed_days=4, target_days=10, activity_persona="active")

    first, second, third = account_waves(events, live)

    assert (first.wave, first.started_at, first.ended_at) == (1, iso(0), iso(14))
    assert (first.end_reason, first.worked_days, first.persona) == ("released", 14, "calm")
    assert (second.end_reason, second.ended_at, second.worked_days) == (
        "spam_blocked",
        iso(23),
        3,
    )
    # The running wave: no end yet, its worked days and persona from the live row.
    assert (third.ended_at, third.end_reason, third.worked_days) == (None, None, 4)
    assert (third.target_days, third.persona) == (10, "active")


def test_an_operator_stop_and_a_dead_session_end_a_wave_too() -> None:
    events = [
        row("warming_started", 0),
        row("warming_stopped", 2, reason="operator", worked_days=2),
        row("warming_started", 5, stint=5),
        row("session_dead", 9, stint=5, status="unauthorized"),
    ]

    stopped, dead = account_waves(events, None)

    assert (stopped.end_reason, stopped.worked_days) == ("stopped", 2)
    assert (dead.end_reason, dead.wave) == ("session_dead", 2)


def test_history_from_before_wave_journalling_ends_at_the_next_start() -> None:
    events = [row("warming_started", 0), row("warming_started", 6, stint=6)]

    old, new = account_waves(events, None)

    assert (old.end_reason, old.ended_at, old.worked_days) == ("replaced", iso(6), None)
    assert new.end_reason is None


def test_a_never_warmed_account_has_no_waves() -> None:
    assert account_waves([], None) == []
    assert account_waves([row("spambot_verdict", 1, stint=None, status="clean")], None) == []


@pytest.mark.asyncio
async def test_the_loader_shows_only_the_current_life() -> None:
    # The deleted predecessor's waves stay in the journal but not in this account's history.
    await append_lifecycle_event("acc-1", "warming_started", {"wave": 1}, LifecycleSnapshot())
    await append_lifecycle_event("acc-1", "deleted", {"reason": "retired"}, LifecycleSnapshot())
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="active", started_at=iso(40), warmed_days=1)
    )
    await append_lifecycle_event(
        "acc-1", "warming_started", {"wave": 1}, LifecycleSnapshot(stint_started_at=iso(40))
    )

    history = await load_account_waves("acc-1")

    assert history is not None
    assert [(wave.wave, wave.started_at, wave.worked_days) for wave in history.waves] == [
        (1, iso(40), 1),
    ]
    assert await load_account_waves("ghost") is None
