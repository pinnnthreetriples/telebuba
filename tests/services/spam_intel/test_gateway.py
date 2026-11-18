"""The gateway end to end on a real database: the five paths every caller meets.

Fresh cache, needs a probe, the budget refuses, the probe fails, the reply is unreadable —
plus coalescing, single-flight, a restart, and the master switch.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import get_spam_status, upsert_spam_status
from core.repositories.spam_probe_ledger import fetch_probe_state, load_budget_snapshot
from schemas.spam_status import SpamStatusVerdict, is_unasked
from services import spam_intel, spam_status
from services.spam_intel import _gateway
from tests.services.spam_intel.conftest import CLEAN_TEXT, LIMITED_TEXT

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.services.spam_intel.conftest import Clock, Events, FakeProbe

    MakeAccount = Callable[..., Awaitable[None]]

_LONG_AGO = "2000-01-01T00:00:00+00:00"


async def _snapshot(account_id: str) -> object:
    return await load_budget_snapshot(account_id, None, _LONG_AGO)


@pytest.mark.asyncio
async def test_a_fresh_cached_reading_is_served_without_asking(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("acc-1")
    await upsert_spam_status(
        SpamStatusVerdict(account_id="acc-1", status="clean", checked_at=clock.now().isoformat())
    )
    verdict = await spam_intel.ask("acc-1")
    assert (verdict.status, verdict.served_from) == ("clean", "cache")
    assert probe.calls == []
    assert await fetch_probe_state("acc-1") is None


@pytest.mark.asyncio
async def test_a_missing_reading_is_probed_stored_and_remembered(
    make_account: MakeAccount, probe: FakeProbe
) -> None:
    await make_account("acc-1")
    with spam_intel.caller_scope("warming.cycle"):
        verdict = await spam_intel.ask("acc-1")
    assert (verdict.status, verdict.served_from) == ("clean", "probe")
    assert probe.calls == ["acc-1"]
    stored = await get_spam_status("acc-1")
    assert stored is not None
    assert stored.served_from is None  # the answer's fields are never stored
    state = await fetch_probe_state("acc-1")
    assert state is not None
    assert (state.last_outcome, state.fail_streak, state.next_allowed_at) == ("clean", 0, None)
    assert (state.last_caller, state.last_success_at is not None) == ("warming.cycle", True)


@pytest.mark.asyncio
async def test_the_ledger_row_names_the_caller_and_the_purpose(make_account: MakeAccount) -> None:
    from core.db import _get_engine  # noqa: PLC0415

    await make_account("acc-1")
    with spam_intel.caller_scope("warming.quarantine"):
        await spam_intel.ask("acc-1", force=True)
    with _get_engine().connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT caller, purpose, outcome, kind, proxy_key IS NOT NULL, finished_at IS NOT NULL"
            " FROM spam_probe_attempts"
        ).one()
    assert tuple(row) == ("warming.quarantine", "decision", "clean", "probe", 1, 1)


@pytest.mark.asyncio
async def test_an_unnamed_caller_is_a_decision_only_when_it_forces(
    make_account: MakeAccount, clock: Clock
) -> None:
    from core.db import _get_engine  # noqa: PLC0415

    await make_account("acc-1")
    await spam_intel.ask("acc-1")
    clock.advance(hours=1)
    await spam_intel.ask("acc-1", force=True)
    with _get_engine().connect() as connection:
        purposes = [
            r[0]
            for r in connection.exec_driver_sql(
                "SELECT purpose FROM spam_probe_attempts ORDER BY id"
            )
        ]
    assert purposes == ["routine", "decision"]


@pytest.mark.asyncio
async def test_a_forced_ask_inside_the_window_gets_the_same_reading(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("acc-1")
    first = await spam_intel.ask("acc-1", force=True)
    clock.advance(minutes=10)
    second = await spam_intel.ask("acc-1", force=True)
    assert (first.served_from, second.served_from) == ("probe", "coalesced")
    assert second.status == first.status
    assert probe.calls == ["acc-1"]


@pytest.mark.asyncio
async def test_a_forced_ask_after_the_window_probes_again(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("acc-1")
    await spam_intel.ask("acc-1", force=True)
    clock.advance(minutes=settings.spam_intel.coalesce_minutes + 1)
    again = await spam_intel.ask("acc-1", force=True)
    assert again.served_from == "probe"
    assert probe.calls == ["acc-1", "acc-1"]


@pytest.mark.asyncio
async def test_simultaneous_forced_asks_share_one_probe(
    make_account: MakeAccount, probe: FakeProbe
) -> None:
    """Five forced callers on one account (the "check channels" shape) cost ONE question."""
    await make_account("acc-1")
    probe.delay = 0.05
    answers = await asyncio.gather(*(spam_intel.ask("acc-1", force=True) for _ in range(5)))
    assert probe.calls == ["acc-1"]
    assert sorted(a.served_from or "" for a in answers) == ["coalesced"] * 4 + ["probe"]
    assert {a.status for a in answers} == {"clean"}


@pytest.mark.asyncio
async def test_different_accounts_are_not_serialised_by_the_single_flight(
    make_account: MakeAccount, probe: FakeProbe
) -> None:
    await make_account("acc-1")
    await make_account("acc-2")
    probe.delay = 0.02
    await asyncio.gather(spam_intel.ask("acc-1", force=True), spam_intel.ask("acc-2", force=True))
    assert sorted(probe.calls) == ["acc-1", "acc-2"]


@pytest.mark.asyncio
async def test_a_leader_that_fails_releases_its_waiters_and_the_next_ask(
    make_account: MakeAccount, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("acc-1")
    boom = {"armed": True}

    async def _flaky(account_id: str, *, previous: SpamStatusVerdict | None = None) -> object:
        if boom["armed"]:
            boom["armed"] = False
            await asyncio.sleep(0.02)
            msg = "executor blew up"
            raise RuntimeError(msg)
        return await real(account_id, previous=previous)

    real = spam_status.probe_and_store
    monkeypatch.setattr(spam_status, "probe_and_store", _flaky)
    leader = asyncio.create_task(spam_intel.ask("acc-1", force=True))
    await asyncio.sleep(0)
    waiter = asyncio.create_task(spam_intel.ask("acc-1", force=True))
    with pytest.raises(RuntimeError, match="executor blew up"):
        await leader
    # The waiter was not poisoned: it asked for itself (inside the window of the failed
    # attempt, so it is handed that failure rather than a second question).
    answer = await waiter
    assert answer.status == "unknown"
    assert _gateway._INFLIGHT == {}
    state = await fetch_probe_state("acc-1")
    assert state is None  # a crash leaves the attempt row, not a verdict, and no streak
    snapshot = await load_budget_snapshot("acc-1", None, _LONG_AGO)
    assert snapshot.last_attempt_started_at is not None


@pytest.mark.asyncio
async def test_a_failed_probe_is_unknown_for_the_caller_and_backs_off(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("acc-1")
    probe.error = "SessionRevokedError"
    verdict = await spam_intel.ask("acc-1", force=True)
    assert (verdict.status, verdict.detail, verdict.served_from) == (
        "unknown",
        "SessionRevokedError",
        "probe",
    )
    assert not is_unasked(verdict)
    assert await get_spam_status("acc-1") is None  # failures are never cached
    state = await fetch_probe_state("acc-1")
    assert state is not None
    assert (state.last_outcome, state.fail_streak, state.last_error) == (
        "error",
        1,
        "SessionRevokedError",
    )
    assert state.next_allowed_at is not None
    # The window hands the failure back instead of asking again.
    again = await spam_intel.ask("acc-1", force=True)
    assert (again.status, again.detail, again.served_from) == (
        "unknown",
        "SessionRevokedError",
        "coalesced",
    )
    assert probe.calls == ["acc-1"]
    # Past the window the backoff holds, and says until when.
    clock.advance(hours=1)
    waiting = await spam_intel.ask("acc-1", force=True)
    assert (waiting.served_from, waiting.reason) == ("deferred", "backoff")
    assert waiting.retry_at is not None
    assert is_unasked(waiting)
    assert probe.calls == ["acc-1"]


@pytest.mark.asyncio
async def test_backoff_grows_with_each_consecutive_failure_and_a_reading_resets_it(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    await make_account("acc-1")
    probe.error = "TimeoutError"
    streaks: list[int] = []
    for hours in (0, 7, 13):
        clock.advance(hours=hours)
        await spam_intel.ask("acc-1", force=True)
        state = await fetch_probe_state("acc-1")
        assert state is not None
        streaks.append(state.fail_streak)
    assert streaks == [1, 2, 3]
    probe.error = None
    clock.advance(hours=25)
    await spam_intel.ask("acc-1", force=True)
    state = await fetch_probe_state("acc-1")
    assert state is not None
    assert (state.fail_streak, state.next_allowed_at, state.last_outcome) == (0, None, "clean")


@pytest.mark.asyncio
async def test_an_unreadable_reply_counts_as_a_failure_for_the_backoff(
    make_account: MakeAccount, probe: FakeProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("acc-1")
    monkeypatch.setattr(settings.spam_intel, "strict_parser", True)
    probe.reply = "Un texto que ningún marcador reconoce."
    verdict = await spam_intel.ask("acc-1", force=True)
    assert (verdict.status, verdict.detail) == ("unknown", "unrecognized_reply")
    state = await fetch_probe_state("acc-1")
    assert state is not None
    assert (state.fail_streak, state.last_outcome) == (1, "unknown")


@pytest.mark.asyncio
async def test_unknown_is_never_clean_under_the_strict_parser_and_still_is_under_the_legacy_one(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_account("acc-1")
    probe.reply = "Un texto que ningún marcador reconoce."
    legacy = await spam_intel.ask("acc-1", force=True)
    assert legacy.status == "clean"  # the pre-gateway fallback, kept until the switch flips
    clock.advance(hours=1)
    monkeypatch.setattr(settings.spam_intel, "strict_parser", True)
    strict = await spam_intel.ask("acc-1", force=True)
    assert strict.status == "unknown"


@pytest.mark.asyncio
async def test_an_account_that_does_not_exist_is_refused_without_a_probe(probe: FakeProbe) -> None:
    verdict = await spam_intel.ask("ghost", force=True)
    assert (verdict.status, verdict.served_from, verdict.reason) == (
        "unknown",
        "refused",
        "account_missing",
    )
    assert verdict.detail == "account not found"
    assert probe.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["unauthorized", "session_error", "account_error", "frozen"])
async def test_an_account_whose_session_is_dead_is_never_asked(
    make_account: MakeAccount, probe: FakeProbe, status: str
) -> None:
    await make_account("acc-1", status=status)
    verdict = await spam_intel.ask("acc-1", force=True)
    assert (verdict.served_from, verdict.reason) == ("refused", "account_not_alive")
    assert probe.calls == []


@pytest.mark.asyncio
async def test_an_account_without_a_proxy_is_never_asked(
    make_account: MakeAccount, probe: FakeProbe, events: Events
) -> None:
    await make_account("acc-1", proxy=None)
    verdict = await spam_intel.ask("acc-1", force=True)
    assert (verdict.served_from, verdict.reason) == ("refused", "no_proxy")
    assert probe.calls == []
    assert events.of("spam_intel_probe_refused")[0]["reason"] == "no_proxy"


@pytest.mark.asyncio
async def test_a_refusal_is_logged_once_per_window_not_per_poll(
    make_account: MakeAccount, events: Events
) -> None:
    await make_account("acc-1", proxy=None)
    for _ in range(5):
        await spam_intel.ask("acc-1", force=True)
    assert len(events.of("spam_intel_probe_refused")) == 1


@pytest.mark.asyncio
async def test_the_budget_survives_a_restart(
    make_account: MakeAccount, probe: FakeProbe, clock: Clock
) -> None:
    """In-memory state is gone after a restart; the ledger is not."""
    await make_account("acc-1")
    await spam_intel.ask("acc-1", force=True)
    _gateway.reset_for_tests()  # a new process: no in-flight map, no log throttle
    clock.advance(minutes=5)
    answer = await spam_intel.ask("acc-1", force=True)
    assert answer.served_from == "coalesced"
    assert probe.calls == ["acc-1"]


@pytest.mark.asyncio
async def test_a_clean_probe_lifts_a_spam_block_through_the_gateway(
    make_account: MakeAccount, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifted: list[str] = []

    async def _lift(account_id: str, verdict: SpamStatusVerdict) -> None:
        lifted.append(f"{account_id}:{verdict.status}")

    monkeypatch.setattr(spam_status, "lift_on_clean", _lift)
    await make_account("acc-1")
    await spam_intel.ask("acc-1", force=True)
    assert lifted == ["acc-1:clean"]


@pytest.mark.asyncio
async def test_a_limited_reading_is_stored_with_its_detail_and_is_not_a_failure(
    make_account: MakeAccount, probe: FakeProbe
) -> None:
    await make_account("acc-1")
    probe.reply = LIMITED_TEXT
    verdict = await spam_intel.ask("acc-1", force=True)
    assert (verdict.status, verdict.detail) == ("limited", "until 29 Oct 2026")
    state = await fetch_probe_state("acc-1")
    assert state is not None
    assert (state.fail_streak, state.next_allowed_at) == (0, None)


@pytest.mark.asyncio
async def test_with_the_gateway_off_every_forced_caller_probes_as_before(
    make_account: MakeAccount, probe: FakeProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.spam_intel, "enabled", False)
    await make_account("acc-1")  # with a proxy: one without is refused in both paths (F-002)
    first = await spam_intel.ask("acc-1", force=True)
    second = await spam_intel.ask("acc-1", force=True)
    assert (first.status, second.status) == ("clean", "clean")
    assert probe.calls == ["acc-1", "acc-1"]
    assert (first.served_from, second.served_from) == (None, None)
    assert await fetch_probe_state("acc-1") is None  # no ledger either


@pytest.mark.asyncio
async def test_bound_askers_keep_the_seam_signature(make_account: MakeAccount) -> None:
    await make_account("acc-1")
    ask = spam_intel.bound_asker("warming")
    verdict = await ask("acc-1", force=True)
    assert verdict.status == "clean"
    assert CLEAN_TEXT  # the shared fake reply is what answered
