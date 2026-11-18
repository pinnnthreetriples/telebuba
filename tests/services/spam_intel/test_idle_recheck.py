"""The idle-account re-check: a routine ask under the shared budget, never a probe of its own (D5).

Accounts with a proxy that no warming run and no appeal covers must still be re-asked once their
stored verdict says ``recheck_due`` (an undated limit past its recheck) or ``limit_expired`` (a
dated limit that ended). The pass picks ONE account per tick and hands it to the gateway, which
alone decides; it runs only with ``passive_decisions`` (acting on evidence is a decision).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import upsert_spam_status
from schemas.spam_status import SpamStatusVerdict
from services import _account_owner, spam_intel
from services.spam_intel import _idle_recheck

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator
    from datetime import datetime

    from schemas.spam_status import SpamStatusKind
    from tests.services.spam_intel.conftest import Clock, Events, FakeProbe

    MakeAccount = Callable[..., Awaitable[None]]

_LONG_AGO = timedelta(days=6)  # past ``limited_recheck_days`` (5)


@pytest.fixture(autouse=True)
def _state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings.spam_intel, "passive_decisions", True)
    monkeypatch.setattr(settings.spam_recheck, "enabled", True)
    _idle_recheck._state.held.clear()
    yield
    _idle_recheck._state.held.clear()
    _idle_recheck._state.task = None


async def _legacy(account: str, status: SpamStatusKind, at: datetime, detail: str | None = None):
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id=account, status=status, detail=detail, checked_at=at.isoformat()
        )
    )


async def _undated_limit(account: str, clock: Clock, age: timedelta = _LONG_AGO) -> None:
    seen = clock.now() - age
    await spam_intel.record_evidence(account, "spambot_limited", source="spambot", observed_at=seen)
    await _legacy(account, "limited", seen, "limited, no date")


async def _make(make_account: MakeAccount, account: str, **kwargs: object) -> None:
    await make_account(account, status="alive", **kwargs)


@pytest.mark.asyncio
async def test_an_idle_account_with_an_undated_limit_past_its_recheck_is_asked_as_a_routine_ask(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock, events: Events
) -> None:
    await _make(make_account, "a1")
    await _undated_limit("a1", clock)
    assert await _idle_recheck.run_idle_recheck() == "a1"
    assert probe.calls == ["a1"]
    (row,) = events.of("spam_intel_probe")
    assert (row["caller"], row["purpose"]) == ("spam_intel.idle_recheck", "routine")


@pytest.mark.asyncio
async def test_a_dated_limit_that_ended_is_asked(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await _make(make_account, "a1")
    day = (clock.now() - timedelta(days=3)).date().isoformat()
    await _legacy("a1", "limited", clock.now() - timedelta(days=4), f"until {day}")
    assert await _idle_recheck.run_idle_recheck() == "a1"
    assert probe.calls == ["a1"]


@pytest.mark.asyncio
async def test_nothing_else_is_asked(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await _make(make_account, "fresh")  # a limit seen yesterday: not due yet
    await _undated_limit("fresh", clock, timedelta(days=1))
    await _make(make_account, "clean")  # nothing to re-check
    await _legacy("clean", "clean", clock.now() - timedelta(days=9))
    await _make(make_account, "no_proxy", proxy=None)  # D1: never probed
    await _undated_limit("no_proxy", clock)
    await make_account("not_alive", status="unauthorized")
    await _undated_limit("not_alive", clock)
    await make_account("brand_new")  # "new": no session check has judged it yet
    await _undated_limit("brand_new", clock)
    assert await _idle_recheck.run_idle_recheck() is None
    assert probe.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("switch", ["passive_decisions", "enabled", "recheck"])
async def test_the_pass_is_off_unless_every_switch_is_on(
    make_account: MakeAccount,
    probe: FakeProbe,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
    switch: str,
) -> None:
    await _make(make_account, "a1")
    await _undated_limit("a1", clock)
    target = settings.spam_recheck if switch == "recheck" else settings.spam_intel
    monkeypatch.setattr(target, "enabled" if switch != "passive_decisions" else switch, False)
    assert await _idle_recheck.run_idle_recheck() is None
    await _idle_recheck.start_idle_recheck()
    assert _idle_recheck._state.task is None
    assert probe.calls == []


@pytest.mark.asyncio
async def test_one_account_per_pass_the_longest_waiting_first(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await _make(make_account, "newer")
    await _make(make_account, "older")
    await _undated_limit("newer", clock, timedelta(days=6))
    await _undated_limit("older", clock, timedelta(days=9))
    assert await _idle_recheck.run_idle_recheck() == "older"
    assert probe.calls == ["older"]
    clock.advance(minutes=30)
    # The reading above was a clean one, so the first is done; the next pass takes the other.
    assert await _idle_recheck.run_idle_recheck() == "newer"
    assert probe.calls == ["older", "newer"]


@pytest.mark.asyncio
async def test_the_gateway_decides_and_an_account_it_turned_away_is_left_alone(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await _make(make_account, "busy")
    await _make(make_account, "free")
    await _undated_limit("busy", clock, timedelta(days=9))
    await _undated_limit("free", clock, timedelta(days=6))
    assert _account_owner.try_claim("busy", "chat_broadcast", "run-1") is None

    assert await _idle_recheck.run_idle_recheck() == "busy"  # asked; the gateway said "later"
    assert probe.calls == []
    assert "busy" in _idle_recheck._state.held

    assert await _idle_recheck.run_idle_recheck() == "free"  # the next pass reaches another
    assert probe.calls == ["free"]

    _account_owner.release("busy", "chat_broadcast", "run-1")
    assert await _idle_recheck.run_idle_recheck() is None  # still held: not asked in the hold
    clock.advance(minutes=settings.spam_recheck.hold_minutes + 1)
    assert await _idle_recheck.run_idle_recheck() == "busy"
    assert probe.calls == ["free", "busy"]


@pytest.mark.asyncio
async def test_the_loop_runs_a_pass_per_tick_and_survives_a_failing_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    passes: list[int] = []

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) > 2:
            raise asyncio.CancelledError

    async def _pass() -> str | None:
        passes.append(1)
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(_idle_recheck, "_sleep", _sleep)
    monkeypatch.setattr(_idle_recheck, "run_idle_recheck", _pass)
    with pytest.raises(asyncio.CancelledError):
        await _idle_recheck._run()
    assert sleeps == [settings.spam_recheck.scan_minutes * 60] * 3
    assert len(passes) == 2  # a failing pass never ended the loop


@pytest.mark.asyncio
async def test_it_starts_once_and_stops_cleanly() -> None:
    await _idle_recheck.start_idle_recheck()
    first = _idle_recheck._state.task
    assert first is not None
    await _idle_recheck.start_idle_recheck()
    assert _idle_recheck._state.task is first
    await _idle_recheck.shutdown_idle_recheck()
    assert _idle_recheck._state.task is None
    assert first.cancelled()
    await _idle_recheck.shutdown_idle_recheck()  # stopping twice is fine
