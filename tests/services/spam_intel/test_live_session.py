"""``live_session_embedding``: a live account is probed a jittered moment into its session.

The pool is faked: ``_human.is_live`` stands in for ``core.telegram_client.has_live_client`` and
``_human.sleep`` records the wait and moves the test clock, so nothing sleeps and the ledger's
timestamps show WHEN the probe happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import _get_engine
from core.repositories.spam_probe_ledger import load_budget_snapshot, start_attempt
from schemas.spam_intel import ProbeAttemptStart
from services import spam_intel
from services.spam_intel import _human

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.spam_intel import Purpose
    from tests.services.spam_intel.conftest import Clock, Events, FakeProbe

    MakeAccount = Callable[..., Awaitable[None]]

_LONG_AGO = "2000-01-01T00:00:00+00:00"
_DELAY = 15.0  # the fake rng answers the low end of the range


@dataclass
class FakePool:
    """Which accounts hold a live client; the wait ends a session when told to."""

    live: set[str] = field(default_factory=set)
    waits: list[float] = field(default_factory=list)
    end_session_on_wait: bool = False

    def is_live(self, account_id: str) -> bool:
        return account_id in self.live


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> FakePool:
    fake = FakePool()

    async def _sleep(seconds: float) -> None:
        fake.waits.append(seconds)
        clock.advance(seconds=seconds)
        if fake.end_session_on_wait:
            fake.live.clear()

    monkeypatch.setattr(_human, "is_live", fake.is_live)
    monkeypatch.setattr(_human, "sleep", _sleep)
    return fake


@pytest.fixture
def embedding_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.spam_intel, "live_session_embedding", True)
    monkeypatch.setattr(settings.spam_intel, "live_session_delay_min_seconds", _DELAY)
    monkeypatch.setattr(settings.spam_intel, "live_session_delay_max_seconds", 120.0)


def _attempts() -> list[str]:
    with _get_engine().connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT started_at FROM spam_probe_attempts ORDER BY id"
        ).all()
    return [str(row[0]) for row in rows]


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [True, False])
@pytest.mark.parametrize("purpose", ["routine", "decision", "measurement", "operator"])
async def test_switch_off_probes_at_the_callers_moment_whether_or_not_the_account_is_live(
    make_account: MakeAccount,
    probe: FakeProbe,
    pool: FakePool,
    purpose: Purpose,
    *,
    live: bool,
) -> None:
    """T16: ``live_session_embedding`` off changes nothing."""
    await make_account("acc-1")
    if live:
        pool.live.add("acc-1")

    verdict = await spam_intel.ask("acc-1", purpose=purpose, force=True)

    assert (verdict.served_from, probe.calls, pool.waits) == ("probe", ["acc-1"], [])


@pytest.mark.asyncio
@pytest.mark.usefixtures("embedding_on")
@pytest.mark.parametrize("purpose", ["routine", "decision"])
async def test_a_live_account_is_probed_a_jittered_moment_into_its_session(
    make_account: MakeAccount,
    probe: FakeProbe,
    pool: FakePool,
    clock: Clock,
    purpose: Purpose,
) -> None:
    await make_account("acc-1")
    pool.live.add("acc-1")
    asked_at = clock.now()

    verdict = await spam_intel.ask("acc-1", purpose=purpose, force=True)

    assert (verdict.status, verdict.served_from) == ("clean", "probe")
    assert pool.waits == [_DELAY]
    assert probe.calls == ["acc-1"]
    # The ledger row says when the probe really went out: after the wait, not at the ask.
    assert _attempts() == [(asked_at + timedelta(seconds=_DELAY)).isoformat()]


@pytest.mark.asyncio
@pytest.mark.usefixtures("embedding_on")
async def test_a_routine_ask_for_an_idle_account_is_deferred_not_connected(
    make_account: MakeAccount,
    probe: FakeProbe,
    pool: FakePool,
    clock: Clock,
    events: Events,
) -> None:
    await make_account("acc-1")

    verdict = await spam_intel.ask("acc-1", purpose="routine")

    assert (verdict.status, verdict.served_from, verdict.reason) == (
        "unknown",
        "deferred",
        "no_live_session",
    )
    expected = clock.now() + timedelta(minutes=settings.spam_intel.live_session_retry_minutes)
    assert verdict.retry_at == expected.isoformat()
    assert probe.calls == []
    assert pool.waits == []
    assert _attempts() == []  # nothing spent: no ledger row, no state
    snapshot = await load_budget_snapshot("acc-1", None, _LONG_AGO)
    assert snapshot.last_attempt_started_at is None
    refusal = events.of("spam_intel_probe_refused")
    assert [(row["reason"], row["action"]) for row in refusal] == [("no_live_session", "defer")]


@pytest.mark.asyncio
@pytest.mark.usefixtures("embedding_on")
@pytest.mark.parametrize("purpose", ["decision", "measurement", "operator"])
async def test_an_idle_account_is_still_probed_for_decision_measurement_and_operator(
    make_account: MakeAccount, probe: FakeProbe, pool: FakePool, purpose: Purpose
) -> None:
    await make_account("acc-1")

    verdict = await spam_intel.ask("acc-1", purpose=purpose, force=True)

    assert (verdict.served_from, probe.calls, pool.waits) == ("probe", ["acc-1"], [])


@pytest.mark.asyncio
@pytest.mark.usefixtures("embedding_on")
@pytest.mark.parametrize("purpose", ["measurement", "operator"])
async def test_measurement_and_operator_never_wait_even_for_a_live_account(
    make_account: MakeAccount, probe: FakeProbe, pool: FakePool, purpose: Purpose
) -> None:
    await make_account("acc-1")
    pool.live.add("acc-1")

    verdict = await spam_intel.ask("acc-1", purpose=purpose, force=True)

    assert (verdict.served_from, probe.calls, pool.waits) == ("probe", ["acc-1"], [])


@pytest.mark.asyncio
@pytest.mark.usefixtures("embedding_on")
async def test_a_session_that_ends_during_the_wait_defers_a_routine_ask(
    make_account: MakeAccount, probe: FakeProbe, pool: FakePool
) -> None:
    await make_account("acc-1")
    pool.live.add("acc-1")
    pool.end_session_on_wait = True

    verdict = await spam_intel.ask("acc-1", purpose="routine")

    assert (verdict.served_from, verdict.reason) == ("deferred", "no_live_session")
    assert (probe.calls, _attempts()) == ([], [])


@pytest.mark.asyncio
@pytest.mark.usefixtures("embedding_on")
async def test_a_session_that_ends_during_the_wait_does_not_stop_a_decision(
    make_account: MakeAccount, probe: FakeProbe, pool: FakePool
) -> None:
    await make_account("acc-1")
    pool.live.add("acc-1")
    pool.end_session_on_wait = True

    verdict = await spam_intel.ask("acc-1", purpose="decision", force=True)

    assert (verdict.served_from, probe.calls) == ("probe", ["acc-1"])


@pytest.mark.asyncio
@pytest.mark.usefixtures("embedding_on")
async def test_the_budget_speaks_first_so_a_refused_ask_never_waits_or_defers_for_a_session(
    make_account: MakeAccount, probe: FakeProbe, pool: FakePool
) -> None:
    await make_account("acc-1", proxy=None)  # no proxy: refused, always
    pool.live.add("acc-1")
    await make_account("acc-2", proxy=None)

    live = await spam_intel.ask("acc-1", purpose="routine")
    idle = await spam_intel.ask("acc-2", purpose="routine")

    assert (live.served_from, live.reason) == ("refused", "no_proxy")
    assert (idle.served_from, idle.reason) == ("refused", "no_proxy")
    assert (probe.calls, pool.waits) == ([], [])


@pytest.mark.asyncio
@pytest.mark.usefixtures("embedding_on")
async def test_the_budget_is_asked_again_after_the_wait(
    make_account: MakeAccount,
    probe: FakeProbe,
    pool: FakePool,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Another account's probe lands while this one waits: the fleet spacing now refuses it."""
    await make_account("acc-1")
    await make_account("acc-2")
    pool.live.add("acc-1")
    monkeypatch.setattr(settings.spam_intel, "fleet_gap_min_seconds", 3600.0)
    monkeypatch.setattr(settings.spam_intel, "fleet_gap_max_seconds", 3600.0)

    async def _neighbour_probes_meanwhile(seconds: float) -> None:
        await start_attempt(
            ProbeAttemptStart(
                account_id="acc-2",
                purpose="decision",
                caller="test",
                started_at=clock.now().isoformat(),
            )
        )
        clock.advance(seconds=seconds)

    monkeypatch.setattr(_human, "sleep", _neighbour_probes_meanwhile)

    verdict = await spam_intel.ask("acc-1", purpose="decision", force=True)

    assert (verdict.served_from, verdict.reason) == ("deferred", "fleet_spacing")
    assert probe.calls == []


def test_the_seam_reads_the_real_pool_without_connecting(monkeypatch: pytest.MonkeyPatch) -> None:
    """``is_live`` is ``has_live_client`` itself: a cached connected client, no I/O."""
    from core.telegram_client import _pool  # noqa: PLC0415 - only this test touches the pool

    class _Connected:
        def is_connected(self) -> bool:
            return True

    _pool._reset_for_tests()
    try:
        assert _human.is_live("acc-1") is False
        monkeypatch.setitem(_pool._CLIENTS, ("acc-1", "desktop"), _Connected())
        assert _human.is_live("acc-1") is True
    finally:
        _pool._reset_for_tests()


@pytest.mark.parametrize(
    ("embedding", "live", "purpose", "moment"),
    [
        (False, True, "routine", "now"),
        (False, False, "routine", "now"),
        (True, True, "routine", "wait"),
        (True, True, "decision", "wait"),
        (True, False, "routine", "defer"),
        (True, False, "decision", "now"),
        (True, True, "measurement", "now"),
        (True, False, "measurement", "now"),
        (True, True, "operator", "now"),
        (True, False, "operator", "now"),
    ],
)
def test_the_moment_rule_table(
    monkeypatch: pytest.MonkeyPatch,
    purpose: Purpose,
    moment: str,
    *,
    embedding: bool,
    live: bool,
) -> None:
    monkeypatch.setattr(settings.spam_intel, "live_session_embedding", embedding)
    monkeypatch.setattr(_human, "is_live", lambda _account_id: live)

    assert _human.live_moment("acc-1", purpose, settings.spam_intel) == moment
