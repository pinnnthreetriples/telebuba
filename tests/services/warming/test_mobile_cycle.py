"""A mobile stint warms in its Redroid pod; a desktop stint never touches a pod.

The pod service is a fake that records each call; the iteration around it is the
real one, so the state write, the schedule and the day's totals are what the loop
really settles.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import upsert_warming_state
from schemas._warming_extras import EXTRA_TOGGLE_DEFAULTS
from schemas._warming_visits import VisitKind, VisitPlan
from schemas.emulator import PlaybookPlan, PlaybookResult
from schemas.warming import WarmingCycleRequest, WarmingStateWrite
from services.accounts.devices import DeviceRefusedError
from services.accounts.mobile_emulator import EmulatorBusyError, EmulatorRefusedError
from services.warming import _loop, _mobile_cycle, _mobile_plan, _seams
from services.warming._mobile_cycle import session_seconds

if TYPE_CHECKING:
    from schemas.devices import DeviceRefusalCode
    from schemas.warming import ActivityPersona
from tests.services.warming._mobile_support import (
    _iterate_on,
    _Pod,
    _record,
    _refusal,
    _stops_done,
    _today,
)


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

    assert pod.calls[0] == "open:background", "a warming open shows no window"
    assert pod.calls[1].endswith(":serial"), "the session goes on from the open's own checks"
    assert pod.calls[2:] == ["stop"]
    assert telethon == [], "no MTProto action on a mobile stint"
    record = await _record()
    assert record.state == "sleeping"
    assert record.last_event == "cycle:ok"
    assert record.cycles_completed == 1
    assert record.daily_actions == 4, "reads, reactions and the story glance spend the budget"
    assert record.warmed_days == 1
    stats = await _today()
    assert stats is not None
    assert (stats.cycles, stats.reads, stats.reactions, stats.failures) == (1, 2, 1, 0)


@pytest.mark.asyncio
async def test_a_mobile_stint_with_the_emulator_off_skips_the_cycle_and_says_why(
    pod: _Pod, telethon: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The operator chose the phone: never a silent Telethon cycle in its place."""
    monkeypatch.setattr(settings.emulator, "enabled", False)
    events: list[str] = []

    async def log(_level: str, event: str, **_kwargs: object) -> None:
        events.append(event)

    monkeypatch.setattr(_loop, "log_event", log)

    await _iterate_on("mobile")

    assert telethon == [], "no MTProto action stands in for the phone"
    assert pod.calls == []
    assert "warming_mobile_emulator_off" in events
    record = await _record()
    assert record.last_event == "cycle:skipped"
    assert record.cycles_completed == 0, "a parked visit is no warming progress"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        "emulator_command_timeout",
        "emulator_boot_timeout",
        "emulator_not_running",
        "emulator_field_missing",
        "emulator_screen_timeout",
    ],
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


@pytest.mark.parametrize("code", ["device_code_cooldown", "device_primary_unavailable"])
@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_a_sign_in_that_must_wait_is_a_passing_cause(
    pod: _Pod, code: DeviceRefusalCode
) -> None:
    pod.open_error = DeviceRefusedError(code, retry_after_seconds=60)

    await _iterate_on("mobile")

    record = await _record()
    assert (record.state, record.last_error) == ("sleeping", code)


@pytest.mark.parametrize("code", ["emulator_app_unresponsive", "emulator_screen_unknown"])
@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_an_app_the_open_could_not_read_is_a_passing_cause(pod: _Pod, code: str) -> None:
    pod.open_error = _refusal(code)

    await _iterate_on("mobile")

    record = await _record()
    assert (record.state, record.last_error) == ("sleeping", code)


@pytest.mark.asyncio
async def test_an_app_left_on_a_sign_in_screen_parks_the_account(
    pod: _Pod, telethon: list[str]
) -> None:
    # A frozen app or an unknown screen no longer reads as "signed out": the open
    # raises a passing refusal for them (above). ``signed_in=False`` is left for an app
    # really showing a sign-in screen warming does not sign in from (code, confirm).
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
        account_id: str, duration_sec: int, plan: PlaybookPlan | None = None, **_opened: str
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
        self.extra_toggles = EXTRA_TOGGLE_DEFAULTS


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

    monkeypatch.setattr(_mobile_plan, "load_warming_settings", controls)
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

    monkeypatch.setattr(_mobile_plan, "load_warming_settings", controls)
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

    monkeypatch.setattr(_mobile_plan, "load_warming_settings", controls)
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

    monkeypatch.setattr(_mobile_plan, "load_warming_settings", controls)
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

    monkeypatch.setattr(_mobile_plan, "load_warming_settings", controls)
    steps: list[str] = []

    async def on_step(step: str) -> None:
        steps.append(step)

    result = await _mobile_cycle.run_mobile_cycle(
        WarmingCycleRequest(account_id="acc-1", remaining_actions=4), on_step=on_step
    )
    await _stops_done()

    assert (result.channels_read, result.reactions_sent, result.attempted_actions) == (2, 1, 4)
    assert pod.calls[1] == "run:120:serial", "4 budgeted actions buy 120 s in the app"
    assert steps == ["read"]


@pytest.mark.asyncio
async def test_a_phone_the_operator_took_meanwhile_is_left_running(
    pod: _Pod, telethon: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    pod.signed_in = False
    # The operator opens Control after the open, while the cycle is failing.
    monkeypatch.setattr(_mobile_cycle, "phone_activity", lambda _account_id: "manual")

    await _iterate_on("mobile")
    await _stops_done()

    assert pod.calls == ["open:background"], "the operator's phone is never stopped under them"
    assert not _mobile_cycle._RUNNING, "and it holds no warming slot"
    assert telethon == []
