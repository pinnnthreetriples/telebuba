"""A mobile stint warms in its Redroid pod; a desktop stint never touches a pod.

The pod service is a fake that records each call; the iteration around it is the
real one, so the state write, the schedule and the day's totals are what the loop
really settles.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from core.config import settings
from core.db import fetch_warming_state, upsert_warming_state
from core.repositories.warming_daily_stats import list_warming_daily_stats
from schemas._warming_visits import VisitKind, VisitPlan
from schemas.emulator import OpenMobileResult, PlaybookPlan, PlaybookResult
from schemas.warming import WarmingCycleRequest, WarmingStateWrite
from services import warming
from services.accounts.devices import DeviceRefusedError
from services.accounts.mobile_emulator import EmulatorBusyError, EmulatorRefusedError
from services.warming import _mobile_cycle, _seams
from services.warming._mobile_cycle import session_seconds
from services.warming.daily_stats import stats_day
from tests.services.warming._support import _no_quiet_days, _ok, _seed_warming_account

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from schemas.device_fingerprint import DeviceType
    from schemas.telegram_actions import ActionResult, TelegramAction
    from schemas.warming import ActivityPersona, WarmingStateRecord
    from schemas.warming_daily_stats import WarmingDailyStats


class _Pod:
    """Records the pod calls; ``open_error``/``run_error`` make that step refuse."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.plans: list[PlaybookPlan | None] = []
        self.signed_in = True
        self.open_error: Exception | None = None
        self.run_error: BaseException | None = None
        self.activity = PlaybookResult(channels_read=2, scrolls=9, stories_watched=3, reactions=1)
        # Set to hold the stop until the test releases it.
        self.stop_gate: asyncio.Event | None = None
        self.parks = True

    async def open(self, account_id: str, *, background: bool = False) -> OpenMobileResult:
        self.calls.append("open:background" if background else "open")
        if self.open_error is not None:
            raise self.open_error
        return OpenMobileResult(account_id=account_id, status="started", signed_in=self.signed_in)

    async def run(
        self, account_id: str, duration_sec: int, plan: PlaybookPlan | None = None
    ) -> PlaybookResult:
        del account_id
        self.calls.append(f"run:{duration_sec}")
        self.plans.append(plan)
        if self.run_error is not None:
            raise self.run_error
        return self.activity

    async def stop(self, account_id: str) -> None:
        del account_id
        if self.stop_gate is not None:
            await self.stop_gate.wait()
        self.calls.append("stop")

    async def park(self, account_id: str) -> bool:
        del account_id
        self.calls.append("park")
        return self.parks

    async def stop_parked(self, account_id: str) -> None:
        self.calls.append(f"stop_parked:{account_id}")


@pytest_asyncio.fixture
async def pod(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Pod]:
    """The fake pod, the emulator on, and one warming account ready for a cycle."""
    _no_quiet_days(monkeypatch)
    await _seed_warming_account()
    fake = _Pod()
    monkeypatch.setattr(settings.emulator, "enabled", True)
    monkeypatch.setattr(_mobile_cycle, "open_account_mobile", fake.open)
    monkeypatch.setattr(_mobile_cycle, "run_mobile_activity", fake.run)
    monkeypatch.setattr(_mobile_cycle, "stop_account_pod", fake.stop)
    monkeypatch.setattr(_mobile_cycle, "_STREAKS", {})
    monkeypatch.setattr(_mobile_cycle, "_RUNNING", set())
    monkeypatch.setattr(_mobile_cycle, "_PARKED", {})
    monkeypatch.setattr(_mobile_cycle, "park_account_pod", fake.park)
    monkeypatch.setattr(_mobile_cycle, "stop_parked_pod", fake.stop_parked)
    # Stopped after every visit unless a test parks it.
    monkeypatch.setattr(settings.emulator, "pause_ttl_seconds", 0)
    yield fake
    if fake.stop_gate is not None:
        fake.stop_gate.set()
    await _stops_done()


async def _stops_done() -> None:
    """Let the detached pod stops finish."""
    await asyncio.gather(*_mobile_cycle._DETACHED)


@pytest.fixture
def telethon(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []

    async def gateway(account_id: str, action: TelegramAction, **_kwargs: object) -> ActionResult:
        sent.append(action.action_type)
        return _ok(account_id, action)

    monkeypatch.setattr(_seams, "_gateway_execute", gateway)
    return sent


async def _iterate_on(device: DeviceType) -> None:
    """One real iteration inside a task bound to ``device``, as the loop task is."""

    async def body() -> None:
        _seams.bind_device(device)
        await warming.run_loop_iteration("acc-1")

    await asyncio.create_task(body())


async def _record() -> WarmingStateRecord:
    record = await fetch_warming_state("acc-1")
    assert record is not None
    return record


async def _today() -> WarmingDailyStats | None:
    day = await stats_day("acc-1", datetime.now(UTC))
    rows = await list_warming_daily_stats("acc-1", day)
    return rows[0] if rows else None


def _refusal(code: str) -> EmulatorRefusedError:
    error = EmulatorRefusedError("emulator_not_running")
    error.code = code
    return error


@pytest.mark.asyncio
async def test_a_desktop_stint_warms_through_telethon_and_never_touches_a_pod(
    pod: _Pod, telethon: list[str]
) -> None:
    await _iterate_on("desktop")

    assert telethon, "the Telethon cycle ran"
    assert pod.calls == []


@pytest.mark.asyncio
async def test_a_mobile_stint_opens_its_pod_spends_the_visit_and_stops_it(
    pod: _Pod, telethon: list[str]
) -> None:
    await _iterate_on("mobile")
    await _stops_done()

    assert pod.calls[0] == "open:background", "a warming open never asks for a login code"
    assert pod.calls[1].startswith("run:")
    assert pod.calls[2:] == ["stop"]
    assert telethon == [], "no MTProto action on a mobile stint"
    record = await _record()
    assert record.state == "sleeping"
    assert record.last_event == "cycle:ok"
    assert record.cycles_completed == 1
    assert record.daily_actions == 3, "channels read and reactions spend the day's budget"
    assert record.warmed_days == 1
    stats = await _today()
    assert stats is not None
    assert (stats.cycles, stats.reads, stats.reactions, stats.failures) == (1, 2, 1, 0)


@pytest.mark.asyncio
async def test_a_mobile_stint_with_the_emulator_off_stays_on_telethon(
    pod: _Pod, telethon: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "enabled", False)

    await _iterate_on("mobile")

    assert telethon
    assert pod.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    ["emulator_command_timeout", "emulator_boot_timeout", "emulator_not_running"],
)
async def test_a_passing_cause_fails_the_cycle_and_the_loop_retries(
    pod: _Pod, telethon: list[str], code: str
) -> None:
    pod.run_error = _refusal(code)

    await _iterate_on("mobile")
    await _stops_done()

    assert pod.calls[-1] == "stop"
    assert telethon == [], "no fallback to Telethon"
    record = await _record()
    assert (record.state, record.last_event, record.last_error) == (
        "sleeping",
        "cycle:failed",
        code,
    )
    assert record.next_run_at is not None
    assert record.daily_actions == 0, "a failed visit spends none of the day's budget"
    assert record.warmed_days == 0, "a failed visit is no warming day"
    stats = await _today()
    assert stats is not None
    assert (stats.cycles, stats.reads, stats.failures) == (1, 0, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        "emulator_proxy_missing",
        "emulator_proxy_unsupported",
        "emulator_twofa_missing",
        "emulator_sdk_mismatch",
        "emulator_field_missing",
    ],
)
async def test_a_cause_a_retry_cannot_fix_parks_the_account_in_error(
    pod: _Pod, telethon: list[str], code: str
) -> None:
    pod.open_error = _refusal(code)

    await _iterate_on("mobile")
    await _stops_done()

    assert pod.calls == ["open:background", "stop"]
    record = await _record()
    assert (record.state, record.last_error) == ("error", code)
    assert telethon == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_a_missing_device_parks_the_account_in_error(pod: _Pod) -> None:
    pod.open_error = DeviceRefusedError("device_missing")

    await _iterate_on("mobile")

    record = await _record()
    assert (record.state, record.last_error) == ("error", "device_missing")


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_a_code_pause_is_a_passing_cause(pod: _Pod) -> None:
    pod.open_error = DeviceRefusedError("device_code_cooldown", retry_after_seconds=60)

    await _iterate_on("mobile")

    record = await _record()
    assert (record.state, record.last_error) == ("sleeping", "device_code_cooldown")


@pytest.mark.asyncio
async def test_an_app_left_signed_out_parks_the_account_without_asking_for_a_code(
    pod: _Pod, telethon: list[str]
) -> None:
    pod.signed_in = False

    await _iterate_on("mobile")
    await _stops_done()

    assert pod.calls == ["open:background", "stop"]
    record = await _record()
    assert (record.state, record.last_error) == ("error", "emulator_not_signed_in")
    assert telethon == []


async def _cycles(count: int) -> None:
    """``count`` iterations, the row put back in ``active`` after each like the loop's wake."""
    for _ in range(count):
        await _iterate_on("mobile")
        await _stops_done()
        await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_too_many_failures_in_a_row_park_the_account_in_error(pod: _Pod) -> None:
    pod.run_error = _refusal("emulator_not_running")
    limit = _mobile_cycle._FAILURE_LIMIT

    await _cycles(limit - 1)
    await _iterate_on("mobile")

    record = await _record()
    assert (record.state, record.last_error) == ("error", "emulator_not_running")


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_a_good_visit_starts_the_failure_count_again(pod: _Pod) -> None:
    limit = _mobile_cycle._FAILURE_LIMIT
    pod.run_error = _refusal("emulator_not_running")
    await _cycles(limit - 1)
    pod.run_error = None
    await _cycles(1)
    pod.run_error = _refusal("emulator_not_running")

    await _iterate_on("mobile")

    assert (await _record()).state == "sleeping"


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_a_failed_session_keeps_what_it_did_before_failing(pod: _Pod) -> None:
    pod.run_error = EmulatorRefusedError(
        "emulator_not_running", partial=PlaybookResult(channels_read=3, reactions=1)
    )

    await _iterate_on("mobile")
    await _stops_done()

    assert pod.calls[-1] == "stop"
    record = await _record()
    assert (record.state, record.last_error) == ("sleeping", "emulator_not_running")
    assert record.daily_actions == 4, "what was done is spent from the budget"
    stats = await _today()
    assert stats is not None
    assert (stats.reads, stats.reactions, stats.failures) == (3, 1, 1)


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_a_session_with_nothing_to_do_is_no_warming_day(pod: _Pod) -> None:
    pod.activity = PlaybookResult(skipped=["no_channel", "no_stories"])

    await _iterate_on("mobile")
    await _stops_done()

    assert pod.calls[-1] == "stop"
    record = await _record()
    assert (record.state, record.last_event) == ("sleeping", "cycle:skipped")
    assert (record.cycles_completed, record.warmed_days) == (0, 0)
    stats = await _today()
    assert stats is None or (stats.cycles, stats.failures) == (0, 0)


@pytest.mark.asyncio
async def test_a_pod_busy_with_the_operator_is_left_running_and_skipped(
    pod: _Pod, telethon: list[str]
) -> None:
    pod.open_error = EmulatorBusyError()

    await _iterate_on("mobile")
    await _stops_done()

    assert pod.calls == ["open:background"], "the operator's pod is never stopped"
    record = await _record()
    assert (record.state, record.last_event) == ("sleeping", "cycle:skipped")
    assert record.cycles_completed == 0
    stats = await _today()
    assert stats is None or stats.failures == 0, "not the account's failure"
    assert not _mobile_cycle._RUNNING, "the slot is free again"
    assert telethon == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_a_session_busy_with_the_operator_leaves_the_pod_running(pod: _Pod) -> None:
    pod.run_error = EmulatorBusyError()

    await _iterate_on("mobile")
    await _stops_done()

    assert "stop" not in pod.calls
    assert (await _record()).last_event == "cycle:skipped"


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_no_free_pod_slot_skips_the_cycle_without_a_pod(
    pod: _Pod, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "max_running_pods", 1)
    _mobile_cycle._RUNNING.add("acc-other")

    await _iterate_on("mobile")

    assert pod.calls == []
    record = await _record()
    assert (record.state, record.last_event) == ("sleeping", "cycle:skipped")


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_a_slow_pod_stop_holds_its_slot_but_not_the_cycle(pod: _Pod) -> None:
    pod.stop_gate = asyncio.Event()

    await asyncio.wait_for(_iterate_on("mobile"), timeout=5)

    assert (await _record()).last_event == "cycle:ok", "settled before the pod stopped"
    assert "stop" not in pod.calls
    assert "acc-1" in _mobile_cycle._RUNNING, "the pod counts until it stops"
    pod.stop_gate.set()
    await _stops_done()
    assert pod.calls[-1] == "stop"
    assert not _mobile_cycle._RUNNING


@pytest.mark.asyncio
async def test_a_stop_mid_session_is_not_held_by_the_pod_stop(
    pod: _Pod, telethon: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    pod.stop_gate = asyncio.Event()
    started = asyncio.Event()

    async def endless(
        account_id: str, duration_sec: int, plan: PlaybookPlan | None = None
    ) -> PlaybookResult:
        del account_id, duration_sec, plan
        started.set()
        await asyncio.Event().wait()
        raise AssertionError

    monkeypatch.setattr(_mobile_cycle, "run_mobile_activity", endless)
    task = asyncio.create_task(_iterate_on("mobile"))
    await started.wait()
    task.cancel()
    done, _pending = await asyncio.wait({task}, timeout=1)

    assert done == {task}, "the cancelled cycle ends without waiting for Docker"
    assert task.cancelled()
    assert "stop" not in pod.calls, "the stop is still on its way"
    pod.stop_gate.set()
    await _stops_done()
    assert pod.calls[-1] == "stop"
    assert telethon == []


@pytest.mark.parametrize(
    ("budget", "expected"),
    [(None, 900), (0, 60), (1, 60), (4, 120), (10**6, 900)],
)
def test_a_visit_buys_seconds_by_its_budget_within_the_cap(
    budget: int | None, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "activity_max_seconds", 900)

    assert session_seconds(budget) == expected


class _Controls:
    def __init__(self, *, reactions: bool) -> None:
        self.reactions_enabled = reactions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("persona", "reactions", "expected"),
    [("calm", True, "calm"), ("active", True, "active"), ("active", False, None)],
)
async def test_the_session_plan_follows_the_persona_and_the_reactions_switch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    persona: ActivityPersona,
    reactions: bool,
    expected: ActivityPersona | None,
) -> None:
    async def controls() -> _Controls:
        return _Controls(reactions=reactions)

    monkeypatch.setattr(_mobile_cycle, "load_warming_settings", controls)
    request = WarmingCycleRequest(account_id="acc-1", activity_persona=persona, remaining_actions=6)

    plan = await _mobile_cycle.session_plan(request)

    chance = settings.warming.persona_reaction_probability[expected] if expected else 0.0
    assert plan.reaction_probability == chance
    assert plan.max_actions == 6


@pytest.mark.asyncio
async def test_the_session_plan_takes_the_visits_shape_and_the_stories_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def controls() -> _Controls:
        return _Controls(reactions=True)

    monkeypatch.setattr(_mobile_cycle, "load_warming_settings", controls)
    monkeypatch.setattr(settings.warming, "story_view_enabled", False)
    visit = VisitPlan(kind="glance", channels=1, stories_first=True)
    request = WarmingCycleRequest(account_id="acc-1", visit=visit)

    plan = await _mobile_cycle.session_plan(request)

    assert (plan.channels, plan.stories_first, plan.stories) == (1, True, False)
    assert plan.max_actions is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "draw", "query"), [("normal", 0.0, "news"), ("binge", 0.99, None)]
)
async def test_a_visit_beyond_a_glance_looks_at_media_and_may_search(
    monkeypatch: pytest.MonkeyPatch, kind: VisitKind, draw: float, query: str | None
) -> None:
    async def controls() -> _Controls:
        return _Controls(reactions=True)

    monkeypatch.setattr(_mobile_cycle, "load_warming_settings", controls)
    monkeypatch.setattr(settings.emulator, "search_queries", ["news"])
    monkeypatch.setattr(_seams.rng, "random", lambda: draw)
    request = WarmingCycleRequest(account_id="acc-1", visit=VisitPlan(kind=kind))

    plan = await _mobile_cycle.session_plan(request)

    assert plan.media
    assert plan.search_queries == ([query] if query else [])
    assert (plan.join_channels, plan.folders, plan.webapp_bot) == (False, False, None)


@pytest.mark.asyncio
async def test_a_glance_neither_searches_nor_plays_media(monkeypatch: pytest.MonkeyPatch) -> None:
    async def controls() -> _Controls:
        return _Controls(reactions=True)

    monkeypatch.setattr(_mobile_cycle, "load_warming_settings", controls)
    monkeypatch.setattr(_seams.rng, "random", lambda: 0.0)
    request = WarmingCycleRequest(account_id="acc-1", visit=VisitPlan(kind="glance"))

    plan = await _mobile_cycle.session_plan(request)

    assert not plan.media
    assert plan.search_queries == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_a_mobile_stint_hands_its_plan_to_the_session(pod: _Pod) -> None:
    await _iterate_on("mobile")

    [plan] = pod.plans
    assert isinstance(plan, PlaybookPlan)


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_a_good_visit_parks_the_pod_and_the_ttl_stops_it(
    pod: _Pod, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "pause_ttl_seconds", 1)

    await _iterate_on("mobile")
    await asyncio.sleep(0)

    assert pod.calls[-1] == "park", "paused, not stopped: the next visit skips the boot"
    assert "acc-1" in _mobile_cycle._PARKED
    assert "acc-1" in _mobile_cycle._RUNNING, "a parked pod keeps its memory, and its slot"
    await _stops_done()
    assert pod.calls[-1] == "stop_parked:acc-1"
    assert "acc-1" not in _mobile_cycle._RUNNING
    assert "acc-1" not in _mobile_cycle._PARKED


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_the_next_visit_takes_its_parked_pod_back(
    pod: _Pod, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "pause_ttl_seconds", 1)
    monkeypatch.setattr(settings.emulator, "max_running_pods", 1)
    _mobile_cycle._RUNNING.add("acc-1")
    _mobile_cycle._PARKED["acc-1"] = -1  # an earlier visit's timer

    await _iterate_on("mobile")

    assert pod.calls[0] == "open:background", "its own slot: no capacity skip"
    assert _mobile_cycle._PARKED.get("acc-1") != -1, "the old timer no longer owns it"


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_a_full_host_stops_the_longest_parked_pod_first(
    pod: _Pod, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "max_running_pods", 2)
    _mobile_cycle._RUNNING.update({"acc-old", "acc-new"})
    _mobile_cycle._PARKED.update({"acc-old": 1, "acc-new": 2})

    await _iterate_on("mobile")

    assert pod.calls[0] == "stop_parked:acc-old"
    assert pod.calls[1] == "open:background"
    assert "acc-old" not in _mobile_cycle._PARKED
    assert "acc-new" in _mobile_cycle._PARKED


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_a_pod_that_will_not_park_is_stopped(
    pod: _Pod, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "pause_ttl_seconds", 1)
    pod.parks = False

    await _iterate_on("mobile")
    await _stops_done()

    assert pod.calls[-2:] == ["park", "stop"]
    assert "acc-1" not in _mobile_cycle._RUNNING


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_a_failed_visit_stops_the_pod_never_parks_it(
    pod: _Pod, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "pause_ttl_seconds", 1)
    pod.run_error = EmulatorRefusedError("emulator_field_missing")

    await _iterate_on("mobile")
    await _stops_done()

    assert "park" not in pod.calls
    assert pod.calls[-1] == "stop"


@pytest.mark.asyncio
async def test_a_cycle_counts_reads_and_reactions_and_reports_the_read_step(
    pod: _Pod, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def controls() -> _Controls:
        return _Controls(reactions=True)

    monkeypatch.setattr(_mobile_cycle, "load_warming_settings", controls)
    steps: list[str] = []

    async def on_step(step: str) -> None:
        steps.append(step)

    result = await _mobile_cycle.run_mobile_cycle(
        WarmingCycleRequest(account_id="acc-1", remaining_actions=4), on_step=on_step
    )
    await _stops_done()

    assert (result.channels_read, result.reactions_sent, result.attempted_actions) == (2, 1, 3)
    assert pod.calls[1] == "run:120", "4 budgeted actions buy 120 s in the app"
    assert steps == ["read"]
@pytest.mark.asyncio
async def test_a_phone_the_operator_took_meanwhile_is_left_running(
    pod: _Pod, telethon: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    pod.signed_in = False
    # The operator opens Control after the open, while the cycle is failing.
    monkeypatch.setattr(_mobile_cycle, "phone_activity", lambda _account_id: "manual")

    await _iterate_on("mobile")

    assert pod.calls == ["open"], "the operator's phone is never stopped under them"
    assert telethon == []
