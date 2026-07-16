"""Warming tests split from the former service test module: test_loop_quarantine.py."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from core.config import settings
from core.db import (
    create_account,
    fetch_warming_state,
    save_warming_settings,
    upsert_spam_status,
    upsert_warming_state,
)
from schemas.accounts import AccountCreate
from schemas.spam_status import SpamStatusVerdict
from schemas.warming import (
    WarmingCycleRequest,
    WarmingStateWrite,
    WarmingStateWriteResult,
)
from services import warming
from services.warming import _loop, _runtime, _seams
from tests.services.warming._support import (
    _Recorder,
    _seed_channel,
    _seed_ready_account,
    _set_settings,
    _verdict,
)


@pytest.mark.asyncio
async def test_cycle_reports_peer_flood(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    recorder.peer_flood_on.add("join_channel")
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "peer_flood"
    assert "read_channel" not in recorder.types()


@pytest.mark.asyncio
async def test_loop_iteration_quarantines_on_peer_flood(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    recorder.peer_flood_on.add("join_channel")
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")
    await create_account(AccountCreate(account_id="acc-1"))

    await warming.run_loop_iteration("acc-1")

    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state == "quarantine"


@pytest.mark.asyncio
async def test_loop_iteration_persists_live_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=True, key="")
    await create_account(AccountCreate(account_id="acc-1"))

    active_actions: list[str | None] = []
    real_set_state = _loop._set_state

    async def _spy(account_id: str, state: str, **kwargs: object) -> WarmingStateWriteResult:
        if state == "active":
            active_actions.append(cast("str | None", kwargs.get("last_action")))
        return await real_set_state(account_id, state, **kwargs)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(_loop, "_set_state", _spy)

    await warming.run_loop_iteration("acc-1")

    # cycle_started seeds set_online; the monotonic hook advances the rail forward
    # only (no backward bounce across the per-channel join/read/react). The exact
    # tail depends on the daily-action cap, so assert a forward-only prefix that
    # has at least reached the channel-read step.
    assert active_actions == list(_loop._PROGRESS_STEPS[: len(active_actions)])
    assert "read" in active_actions


@pytest.mark.asyncio
async def test_loop_iteration_survives_progress_write_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A raising progress write (e.g. a transient SQLite lock) must not abort the
    # cycle or park a healthy account in error — the hook is cosmetic, best-effort.
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")
    await create_account(AccountCreate(account_id="acc-1"))

    real_set_state = _loop._set_state

    async def _flaky(account_id: str, state: str, **kwargs: object) -> WarmingStateWriteResult:
        # Only the mid-cycle progress writes blow up (state=active, no last_event);
        # the cycle_started / finalize boundary writes go through untouched.
        if state == "active" and kwargs.get("last_event") is None:
            msg = "database is locked"
            raise RuntimeError(msg)
        return await real_set_state(account_id, state, **kwargs)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(_loop, "_set_state", _flaky)

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "ok"
    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state != "error"


@pytest.mark.asyncio
async def test_loop_iteration_clears_stale_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    # The rail advances last_action live but never updates last_channel, so a
    # prior cycle's failed channel must be cleared at cycle start, not left to
    # surface stale under a fresh active step.
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    # enforce_readiness off: this test is about stale-channel clearing, not the
    # П3 readiness gate (the bare account would otherwise be parked).
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="sleeping", last_channel="old-chan"),
    )

    active_channels: list[str | None] = []
    real_set_state = _loop._set_state

    async def _spy(account_id: str, state: str, **kwargs: object) -> WarmingStateWriteResult:
        write = await real_set_state(account_id, state, **kwargs)  # ty: ignore[invalid-argument-type]
        if state == "active":
            active_channels.append(write.record.last_channel)
        return write

    monkeypatch.setattr(_loop, "_set_state", _spy)

    await warming.run_loop_iteration("acc-1")

    assert active_channels  # the cycle reached at least the cycle_started write
    assert "old-chan" not in active_channels


@pytest.mark.asyncio
async def test_no_channels_cycle_does_not_increment_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A cycle that finds no channels is a no-op skip — it must not bump
    # cycles_completed (false progress) (audit П9). enforce_readiness is off so
    # the loop reaches the cycle's skip path rather than the П3 readiness gate.
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "skipped"
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.cycles_completed == 0
    assert record.state == "sleeping"


@pytest.mark.asyncio
async def test_run_loop_iteration_parks_degraded_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A running account that degrades mid-warming (spam-limited here) must be
    # parked to error when enforce_readiness is on — not warmed on while the
    # card already shows the blocker (audit П3).
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_ready_account("acc-1")
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=True,
        gemini_api_key="",
    )
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id="acc-1",
            status="limited",
            detail="restricted until 2026-12-31",
            checked_at="2026-06-22T00:00:00+00:00",
        ),
    )
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="sleeping"))

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "error"
    assert recorder.actions == []  # no cycle ran
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "error"
    assert "spam" in (record.last_error or "")


@pytest.mark.asyncio
async def test_run_loop_iteration_runs_ready_account_under_enforcement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The gate is a guard, not a blanket block: a still-ready account cycles
    # normally with enforce_readiness on (audit П3).
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_ready_account("acc-1")
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=True,
        gemini_api_key="",
    )
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="sleeping"))

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "ok"
    assert "set_online" in recorder.types()


@pytest.mark.asyncio
async def test_quarantine_recovers_when_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="quarantine", quarantine_count=1),
    )

    async def fake_refresh(account_id: str, *, force: bool = False) -> SpamStatusVerdict:  # noqa: ARG001
        return _verdict(account_id, "clean")

    monkeypatch.setattr(_seams, "refresh_spam_status", fake_refresh)

    result = await warming.run_loop_iteration("acc-1")

    assert result.detail == "recovered"
    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state == "sleeping"
    assert state.quarantine_count == 0


@pytest.mark.asyncio
async def test_quarantine_extends_when_still_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "quarantine_max_repeats", 3)
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="quarantine", quarantine_count=0),
    )

    async def fake_refresh(account_id: str, *, force: bool = False) -> SpamStatusVerdict:  # noqa: ARG001
        return _verdict(account_id, "limited")

    monkeypatch.setattr(_seams, "refresh_spam_status", fake_refresh)

    await warming.run_loop_iteration("acc-1")

    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state == "quarantine"
    assert state.quarantine_count == 1


@pytest.mark.asyncio
async def test_quarantine_exhausts_to_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "quarantine_max_repeats", 3)
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="quarantine", quarantine_count=2),
    )

    async def fake_refresh(account_id: str, *, force: bool = False) -> SpamStatusVerdict:  # noqa: ARG001
        return _verdict(account_id, "limited")

    monkeypatch.setattr(_seams, "refresh_spam_status", fake_refresh)

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "error"
    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state == "error"


@pytest.mark.asyncio
async def test_loop_quarantine_past_target_recovers_not_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Bug #1: a quarantined (peer-flooded) account that crosses target_days must
    # NOT be parked ``warming_complete`` — that would present a still-restricted
    # account as promotable and let it be graduated into the neurocomment pool.
    # The target-reached gate must defer to the quarantine recovery probe.
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_ready_account("acc-1")
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)

    probe_calls: list[str] = []

    async def fake_probe(account_id: str, *, force: bool = False) -> SpamStatusVerdict:  # noqa: ARG001
        probe_calls.append(account_id)
        return SpamStatusVerdict(
            account_id=account_id,
            status="clean",
            detail=None,
            checked_at="2026-01-01T00:00:00+00:00",
        )

    monkeypatch.setattr(_seams, "refresh_spam_status", fake_probe)

    old_start = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="quarantine",
            started_at=old_start,
            target_days=3,
            quarantine_count=0,
        ),
    )

    result = await warming.run_loop_iteration("acc-1")

    # The quarantine recovery probe ran — not the target-complete park.
    assert probe_calls == ["acc-1"]
    assert recorder.actions == []
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.last_event != "warming_complete"
    assert record.last_event == "quarantine_recovered"
    assert record.state == "sleeping"
    assert result.detail == "recovered"


@pytest.mark.asyncio
async def test_stale_quarantine_cas_failure_prevents_spam_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-5 P1: a stale quarantine recovery must not hit @SpamBot.

    Round-4 P1.2 closed the regular cycle path, but ``_recover_from_quarantine``
    issued ``_seams.refresh_spam_status(account_id, force=True)`` *before*
    any CAS write — so a stale loop in the quarantine branch would still
    perform external Telegram I/O on behalf of a generation that the
    operator had already replaced.

    Force the race: DB row carries the new generation (``run-b``); lie to
    the iteration's pre-cycle guard so it sees ``run-a`` and steps into
    ``_recover_from_quarantine``. The CAS-gate inside the recovery branch
    must short-circuit before the spam probe fires.
    """
    from services.warming._loop import run_loop_iteration  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1"))
    await _set_settings(chat=False, reactions=False, key="")
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="quarantine",
            quarantine_count=0,
            run_id="run-b",
        ),
    )

    real_fetch = _loop.fetch_warming_state
    fetch_calls = {"n": 0}

    async def fake_fetch(account_id: str):  # type: ignore[no-untyped-def]
        fetch_calls["n"] += 1
        if fetch_calls["n"] == 1:
            real = await real_fetch(account_id)
            if real is None:
                return real
            return real.model_copy(update={"run_id": "run-a"})
        return await real_fetch(account_id)

    monkeypatch.setattr(_loop, "fetch_warming_state", fake_fetch)

    probe_calls: list[str] = []

    async def fake_probe(account_id: str, *, force: bool = False) -> SpamStatusVerdict:  # noqa: ARG001
        probe_calls.append(account_id)
        return SpamStatusVerdict(
            account_id=account_id,
            status="clean",
            detail=None,
            checked_at="2026-01-01T00:00:00+00:00",
        )

    monkeypatch.setattr(_seams, "refresh_spam_status", fake_probe)

    result = await run_loop_iteration("acc-1", run_id="run-a")
    assert result.status == "skipped"
    assert result.detail == "stale run"
    assert probe_calls == []


@pytest.mark.asyncio
async def test_reconcile_resumes_quarantine_recovery_despite_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reconcile readiness gate must not abort an engine-managed quarantine recovery."""
    started: list[str] = []

    async def fake_loop(account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        started.append(account_id)
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=True,
        gemini_api_key="",
    )
    # No proxy => evaluate_readiness would fail, but quarantine is engine-managed
    # recovery and must keep running so it can re-probe and recover/escalate.
    await create_account(AccountCreate(account_id="acc-q"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-q", state="quarantine"))

    await warming.reconcile_warming_runtime()

    assert "acc-q" in warming._RUNTIME
    record = await fetch_warming_state("acc-q")
    assert record is not None
    assert record.state == "quarantine"  # not parked to error by the readiness gate
