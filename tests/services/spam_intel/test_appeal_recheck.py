"""The appeal re-check: when it is due, that it is an ordinary DECISION ask under the budget."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from core._config_spam_intel import SpamIntelSettings
from core.config import settings
from core.repositories.spam_appeals import fetch_open_appeal
from core.repositories.spam_probe_ledger import load_budget_snapshot
from services import spam_intel
from services.spam_intel import _appeal, _appeal_recheck
from tests.services.spam_intel._appeal_support import (
    DATED_LIMIT,
    TODAY,
    appeal_clock,
    appeal_events,
    store_limited,
)
from tests.services.spam_intel.conftest import CLEAN_TEXT, FakeProbe

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.services.spam_intel.conftest import Clock, Events

    MakeAccount = Callable[..., Awaitable[None]]

__all__ = ["appeal_clock", "appeal_events"]

_UNDATED = "Your account is limited."


@pytest.fixture(autouse=True)
def _fresh_loop_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_appeal_recheck._state, "held", {})
    monkeypatch.setattr(_appeal_recheck._state, "task", None)


async def _filed(make_account: MakeAccount, clock: Clock, detail: str | None) -> None:
    await make_account("a1")
    await store_limited("a1", clock.now(), detail=detail)
    await spam_intel.record_appeal_filed("a1", "anna")


async def _attempts(account_id: str = "a1") -> int:
    snapshot = await load_budget_snapshot(
        account_id, None, (TODAY - timedelta(days=30)).isoformat()
    )
    return snapshot.account_count_24h


# ------------------------------------------------------------------ when it is due


@pytest.mark.asyncio
async def test_an_open_ended_limit_is_due_appeal_recheck_hours_after_filing(
    make_account: MakeAccount, clock: Clock
) -> None:
    await _filed(make_account, clock, _UNDATED)

    state = await spam_intel.appeal_state("a1")

    assert settings.spam_intel.appeal_recheck_hours == 24.0
    assert state.recheck_at == TODAY + timedelta(hours=24)


@pytest.mark.asyncio
async def test_the_hours_are_a_setting(
    make_account: MakeAccount, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.spam_intel, "appeal_recheck_hours", 6.0)
    await _filed(make_account, clock, _UNDATED)

    assert (await spam_intel.appeal_state("a1")).recheck_at == TODAY + timedelta(hours=6)


@pytest.mark.asyncio
async def test_a_dated_limit_is_due_at_its_end_day_plus_a_stable_jitter(
    make_account: MakeAccount, clock: Clock
) -> None:
    await _filed(make_account, clock, DATED_LIMIT)

    first = (await spam_intel.appeal_state("a1")).recheck_at
    again = (await spam_intel.appeal_state("a1")).recheck_at

    assert first is not None
    assert first == again  # drawn once from the row, not per read
    # 29 Nov 2026 is the day; the limit holds until one hour past the next midnight.
    ends = TODAY.replace(day=29, hour=0) + timedelta(days=1, hours=1)
    assert ends <= first <= ends + timedelta(hours=settings.spam_intel.appeal_recheck_jitter_hours)


@pytest.mark.asyncio
async def test_no_jitter_means_exactly_the_end(
    make_account: MakeAccount, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.spam_intel, "appeal_recheck_jitter_hours", 0.0)
    await _filed(make_account, clock, DATED_LIMIT)

    ends = TODAY.replace(day=29, hour=0) + timedelta(days=1, hours=1)
    assert (await spam_intel.appeal_state("a1")).recheck_at == ends


@pytest.mark.asyncio
async def test_the_jitter_differs_between_appeals_so_a_fleet_is_not_asked_together(
    make_account: MakeAccount, clock: Clock
) -> None:
    times = set()
    for index in range(6):
        account = f"a{index}"
        await make_account(account)
        await store_limited(account, clock.now())
        times.add((await spam_intel.record_appeal_filed(account, "anna")).recheck_at)
    assert len(times) > 1


def test_the_setting_is_validated() -> None:
    assert SpamIntelSettings().appeal_recheck_hours == 24.0
    for field, bad in (
        ("appeal_recheck_hours", 0.0),
        ("appeal_recheck_jitter_hours", -1.0),
        ("appeal_scan_minutes", 0.0),
    ):
        with pytest.raises(ValueError, match=field):
            SpamIntelSettings.model_validate({field: bad})
    with pytest.raises(ValueError, match="appeal_dialog_target"):
        SpamIntelSettings.model_validate({"appeal_dialog_target": "elsewhere"})
    assert _appeal.recheck_at  # the pure rule is public for the card and the loop


@pytest.mark.asyncio
async def test_nothing_is_due_before_its_time_and_it_is_due_after(
    make_account: MakeAccount, clock: Clock
) -> None:
    await _filed(make_account, clock, _UNDATED)

    clock.advance(hours=23)
    assert await _appeal_recheck.due_appeals(clock.now()) == []
    clock.advance(hours=2)
    assert await _appeal_recheck.due_appeals(clock.now()) == ["a1"]


@pytest.mark.asyncio
async def test_a_closed_appeal_is_never_due(make_account: MakeAccount, clock: Clock) -> None:
    await _filed(make_account, clock, _UNDATED)
    clock.advance(hours=30)
    await spam_intel.ask("a1", force=True, purpose="decision", caller="test")  # clean: closes it

    assert await _appeal_recheck.due_appeals(clock.now()) == []
    assert await fetch_open_appeal("a1") is None


@pytest.mark.asyncio
async def test_a_frozen_account_is_not_re_checked(make_account: MakeAccount, clock: Clock) -> None:
    await _filed(make_account, clock, _UNDATED)
    await spam_intel.record_evidence("a1", "freeze_seen", source="session_check")
    clock.advance(hours=30)

    assert await _appeal_recheck.due_appeals(clock.now()) == []


# ------------------------------------------------- an ordinary decision under the budget


@pytest.mark.asyncio
async def test_the_recheck_is_one_decision_ask_and_a_clean_answer_ends_the_appeal(
    make_account: MakeAccount, clock: Clock, probe: FakeProbe, events: Events
) -> None:
    await _filed(make_account, clock, _UNDATED)
    clock.advance(hours=25)
    probe.reply = CLEAN_TEXT

    assert await _appeal_recheck.run_appeal_rechecks() == 1

    assert probe.calls == ["a1"]
    assert (await spam_intel.appeal_state("a1")).status == "closed"
    assert events.of("spam_intel_probe")[0]["caller"] == _appeal_recheck.CALLER
    assert events.of("spam_intel_probe")[0]["purpose"] == "decision"
    # Closed: the next pass has nothing to do and asks nothing.
    assert await _appeal_recheck.run_appeal_rechecks() == 0
    assert probe.calls == ["a1"]


@pytest.mark.asyncio
async def test_a_block_that_stands_is_not_asked_about_again_and_again(
    make_account: MakeAccount, clock: Clock, probe: FakeProbe
) -> None:
    """Due once: after a real answer, however it reads, the usual rechecks take over."""
    await _filed(make_account, clock, _UNDATED)
    clock.advance(hours=25)
    probe.reply = "Your account is limited."

    assert await _appeal_recheck.run_appeal_rechecks() == 1
    for _ in range(3):
        clock.advance(hours=3)
        assert await _appeal_recheck.run_appeal_rechecks() == 0

    assert probe.calls == ["a1"]
    assert (await spam_intel.appeal_state("a1")).status == "open"


@pytest.mark.asyncio
async def test_a_postponed_ask_waits_for_its_retry_time_and_spends_no_probe(
    make_account: MakeAccount,
    clock: Clock,
    probe: FakeProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.spam_intel, "proxy_spacing_minutes", 120.0)
    await make_account("a1", proxy="shared")
    await make_account("other", proxy="shared")
    await store_limited("a1", clock.now(), detail=_UNDATED)
    await spam_intel.record_appeal_filed("a1", "anna")
    clock.advance(hours=25)
    probe.reply = CLEAN_TEXT
    # Another account behind the same proxy was probed a moment ago: the budget says later.
    await spam_intel.ask("other", force=True, purpose="decision", caller="test")
    assert probe.calls == ["other"]

    assert await _appeal_recheck.run_appeal_rechecks() == 1  # asked, and the budget deferred it

    assert probe.calls == ["other"]  # no probe for the appeal
    assert await _attempts("a1") == 0
    held = _appeal_recheck._state.held["a1"]
    assert held > clock.now()

    # Inside the hold nothing is asked at all; past it, it is asked once more.
    clock.advance(minutes=30)
    assert await _appeal_recheck.run_appeal_rechecks() == 0
    clock.at = held + timedelta(seconds=1)
    assert await _appeal_recheck.run_appeal_rechecks() == 1
    assert probe.calls == ["other", "a1"]


@pytest.mark.asyncio
async def test_an_account_without_a_proxy_is_refused_not_probed(
    make_account: MakeAccount, clock: Clock, probe: FakeProbe
) -> None:
    await make_account("a1", proxy=None)
    await store_limited("a1", clock.now(), detail=_UNDATED)
    await spam_intel.record_appeal_filed("a1", "anna")
    clock.advance(hours=25)

    assert await _appeal_recheck.run_appeal_rechecks() == 1

    assert probe.calls == []
    assert (await spam_intel.appeal_state("a1")).status == "open"
    assert _appeal_recheck._state.held["a1"] > clock.now()


@pytest.mark.asyncio
async def test_a_reading_inside_the_coalescing_window_is_reused_not_asked_again(
    make_account: MakeAccount, clock: Clock, probe: FakeProbe
) -> None:
    await _filed(make_account, clock, _UNDATED)
    clock.advance(hours=24, minutes=1)
    probe.reply = "Your account is limited."
    await spam_intel.ask("a1", force=True, purpose="operator", caller="api.operator")
    assert probe.calls == ["a1"]

    # The operator's own check a minute ago is the answer; the appeal is no longer due.
    clock.advance(minutes=1)
    assert await _appeal_recheck.run_appeal_rechecks() == 0
    assert probe.calls == ["a1"]


# ----------------------------------------------------------------------- the loop


@pytest.mark.asyncio
async def test_the_loop_scans_on_its_interval_and_stops_on_shutdown(
    make_account: MakeAccount, clock: Clock, probe: FakeProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _filed(make_account, clock, _UNDATED)
    clock.advance(hours=25)
    probe.reply = CLEAN_TEXT
    slept: list[float] = []
    asked = asyncio.Event()

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)
        if probe.calls:
            asked.set()
            await asyncio.Event().wait()  # park: the test ends the loop

    monkeypatch.setattr(_appeal_recheck, "_sleep", _sleep)
    await spam_intel.start_appeal_recheck()
    await spam_intel.start_appeal_recheck()  # a second start is a no-op
    await asyncio.wait_for(asked.wait(), timeout=10)
    await spam_intel.shutdown_appeal_recheck()

    assert slept[0] == settings.spam_intel.appeal_scan_minutes * 60
    assert probe.calls == ["a1"]
    assert _appeal_recheck._state.task is None
    await spam_intel.shutdown_appeal_recheck()  # and stopping twice is harmless


@pytest.mark.asyncio
async def test_the_loop_does_not_start_while_the_gateway_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.spam_intel, "enabled", False)

    await spam_intel.start_appeal_recheck()

    assert _appeal_recheck._state.task is None


@pytest.mark.asyncio
async def test_one_failed_pass_does_not_end_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    passes = 0
    done = asyncio.Event()

    async def _run_once() -> int:
        nonlocal passes
        passes += 1
        if passes == 1:
            message = "boom"
            raise RuntimeError(message)
        done.set()
        return 0

    async def _sleep(_seconds: float) -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr(_appeal_recheck, "run_appeal_rechecks", _run_once)
    monkeypatch.setattr(_appeal_recheck, "_sleep", _sleep)
    await spam_intel.start_appeal_recheck()
    await asyncio.wait_for(done.wait(), timeout=10)
    await spam_intel.shutdown_appeal_recheck()

    assert passes >= 2
