"""Stop/promote/unmark lifecycle contracts, including partial failures."""

from __future__ import annotations

import asyncio

import pytest

from core.db import create_account, fetch_warming_state, upsert_warming_state
from schemas.accounts import AccountCreate
from schemas.warming import StopWarmingRequest, WarmingStateWrite
from services import warming
from services.warming import _graduation, _runtime


async def _seed_active(*, promoted: bool = False) -> None:
    await create_account(AccountCreate(account_id="acc-1", label="Primary"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="active", run_id="generation-a")
    )
    if promoted:
        await _graduation.mark_promoted_to_nc("acc-1")


async def _wait_forever() -> None:
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_promote_cancels_loop_then_persists_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_active()
    cancelled = asyncio.Event()

    async def loop() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(loop())
    warming._RUNTIME["acc-1"] = task
    await asyncio.sleep(0)
    refreshes = 0

    async def refresh() -> None:
        nonlocal refreshes
        refreshes += 1

    monkeypatch.setattr(_runtime, "_refresh_dialogue_pairs", refresh)

    card = await warming.promote_to_neurocomment("acc-1")

    assert cancelled.is_set()
    assert task.cancelled()
    assert "acc-1" not in warming._RUNTIME
    assert card.state == "idle"
    assert card.promoted_to_nc is True
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "idle"
    assert record.run_id is None
    assert record.last_event == "stopped"
    assert record.promoted_to_nc is True
    assert refreshes == 1


@pytest.mark.asyncio
async def test_repeated_promote_is_state_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    await _seed_active()
    refreshes = 0

    async def refresh() -> None:
        nonlocal refreshes
        refreshes += 1

    monkeypatch.setattr(_runtime, "_refresh_dialogue_pairs", refresh)

    first = await warming.promote_to_neurocomment("acc-1")
    first_record = await fetch_warming_state("acc-1")
    second = await warming.promote_to_neurocomment("acc-1")
    second_record = await fetch_warming_state("acc-1")

    assert first.state == second.state == "idle"
    assert first.promoted_to_nc is second.promoted_to_nc is True
    assert first_record is not None
    assert second_record is not None
    assert second_record.cycles_completed == first_record.cycles_completed
    assert second_record.last_event == "stopped"
    assert warming._RUNTIME == {}
    # Each explicit operator action refreshes the pair graph, even though the DB
    # state is idempotent; this keeps other accounts' pairing view current.
    assert refreshes == 2


@pytest.mark.asyncio
async def test_unmark_is_idempotent_and_does_not_restart_warming() -> None:
    await _seed_active(promoted=True)
    await _graduation._stop_warming_locked("acc-1")

    first = await warming.unmark_neurocomment("acc-1")
    second = await warming.unmark_neurocomment("acc-1")

    assert first.promoted_to_nc is False
    assert second.promoted_to_nc is False
    assert first.state == second.state == "idle"
    assert "acc-1" not in warming._RUNTIME
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.promoted_to_nc is False
    assert record.state == "idle"


@pytest.mark.asyncio
async def test_unmark_unknown_account_does_not_create_stub_row() -> None:
    card = await warming.unmark_neurocomment("ghost")

    assert card.account_id == "ghost"
    assert card.state == "idle"
    assert card.promoted_to_nc is False
    assert await fetch_warming_state("ghost") is None


@pytest.mark.asyncio
async def test_promotion_mark_failure_leaves_account_safely_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two-step operation cannot roll back cancellation; safe idle must stick."""
    await _seed_active()
    task = asyncio.create_task(_wait_forever())
    warming._RUNTIME["acc-1"] = task
    await asyncio.sleep(0)
    events: list[str] = []
    refreshes = 0

    async def fail_mark(_account_id: str) -> None:
        message = "database unavailable"
        raise RuntimeError(message)

    async def log(_level: str, event: str, **_kwargs: object) -> None:
        events.append(event)

    async def refresh() -> None:
        nonlocal refreshes
        refreshes += 1

    monkeypatch.setattr(_graduation, "mark_promoted_to_nc", fail_mark)
    monkeypatch.setattr(_graduation, "log_event", log)
    monkeypatch.setattr(_runtime, "_refresh_dialogue_pairs", refresh)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await warming.promote_to_neurocomment("acc-1")

    assert task.cancelled()
    assert "acc-1" not in warming._RUNTIME
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "idle"
    assert record.run_id is None
    assert record.promoted_to_nc is False
    assert "warming_promoted_to_neurocomment" not in events
    assert refreshes == 0


@pytest.mark.asyncio
async def test_stop_failure_prevents_promotion_and_success_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_active()
    calls: list[str] = []

    async def fail_stop(_account_id: str) -> None:
        calls.append("stop")
        message = "state store unavailable"
        raise RuntimeError(message)

    async def mark(_account_id: str) -> None:
        calls.append("mark")

    async def log(_level: str, event: str, **_kwargs: object) -> None:
        calls.append(event)

    monkeypatch.setattr(_graduation, "_stop_warming_locked", fail_stop)
    monkeypatch.setattr(_graduation, "mark_promoted_to_nc", mark)
    monkeypatch.setattr(_graduation, "log_event", log)

    with pytest.raises(RuntimeError, match="state store unavailable"):
        await warming.promote_to_neurocomment("acc-1")

    assert calls == ["stop"]


@pytest.mark.asyncio
async def test_task_cleanup_error_is_logged_but_stop_still_lands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_active()

    async def broken_loop() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            message = "cleanup failed"
            raise RuntimeError(message) from None

    task = asyncio.create_task(broken_loop())
    warming._RUNTIME["acc-1"] = task
    await asyncio.sleep(0)
    events: list[tuple[str, dict[str, object]]] = []

    async def log(_level: str, event: str, **kwargs: object) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr(_graduation, "log_event", log)

    card = await warming.stop_warming(StopWarmingRequest(account_id="acc-1"))

    assert card.state == "idle"
    assert "acc-1" not in warming._RUNTIME
    names = [event for event, _kwargs in events]
    assert names == ["warming_stop_task_error", "warming_stopped"]
    assert events[0][1]["extra"] == {
        "error_type": "RuntimeError",
        "message": "cleanup failed",
    }


@pytest.mark.asyncio
async def test_unmark_failure_emits_no_success_and_preserves_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_active(promoted=True)
    events: list[str] = []

    async def fail(_account_id: str) -> None:
        message = "write failed"
        raise RuntimeError(message)

    async def log(_level: str, event: str, **_kwargs: object) -> None:
        events.append(event)

    monkeypatch.setattr(_graduation, "unmark_promoted_to_nc", fail)
    monkeypatch.setattr(_graduation, "log_event", log)

    with pytest.raises(RuntimeError, match="write failed"):
        await warming.unmark_neurocomment("acc-1")

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.promoted_to_nc is True
    assert events == []
