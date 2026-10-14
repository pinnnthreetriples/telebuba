"""Visit types (item 4.4): kinds by persona weights, their budgets, and the step-order rules."""

from __future__ import annotations

import random
from collections import Counter

import pytest

from core.config import settings
from core.db import (
    create_account,
    fetch_warming_state,
    record_dialogue_message,
    save_warming_settings,
    upsert_warming_state,
)
from schemas._warming_extras import EXTRA_TOGGLE_DEFAULTS
from schemas._warming_visits import VisitPlan, VisitStep
from schemas.accounts import AccountCreate
from schemas.gemini import GeminiResult
from schemas.warming import (
    AddChannelsRequest,
    WarmingCycleRequest,
    WarmingCycleResult,
    WarmingStateWrite,
)
from services import warming
from services.warming import _cadence, _iteration, _loop, _seams
from services.warming._visits import choose_visit_kind, kind_budget, plan_visit
from tests.services.warming._support import (
    _Recorder,
    _seed_channel,
    _seed_ready_account,
    _seed_two_warming_accounts,
    _set_settings,
)

_CHANNEL_STEPS: tuple[VisitStep, ...] = ("join", "read", "react")


def _rng(seed: int = 7) -> random.Random:
    return random.Random(seed)  # noqa: S311 - deterministic test rng


def test_the_first_days_never_binge() -> None:
    rng = _rng()
    for phase in ("intro", "settling"):
        kinds = {choose_visit_kind("active", phase, rng, dm_waiting=False) for _ in range(300)}
        assert kinds == {"normal", "glance"}
    kinds = {choose_visit_kind("active", "warming", rng, dm_waiting=False) for _ in range(300)}
    assert kinds == {"normal", "glance", "binge"}


def test_reply_only_is_drawn_only_while_a_dm_waits() -> None:
    rng = _rng()
    quiet = {choose_visit_kind("normal", "active", rng, dm_waiting=False) for _ in range(300)}
    waiting = {choose_visit_kind("normal", "active", rng, dm_waiting=True) for _ in range(300)}
    assert "reply" not in quiet
    assert "reply" in waiting


def test_the_persona_weights_shape_the_mix() -> None:
    rng = _rng()
    calm = Counter(choose_visit_kind("calm", "active", rng, dm_waiting=False) for _ in range(2000))
    active = Counter(
        choose_visit_kind("active", "active", rng, dm_waiting=False) for _ in range(2000)
    )
    assert calm["glance"] > active["glance"]
    assert calm["binge"] < active["binge"]


def test_a_persona_without_weights_visits_normally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "persona_visit_weights", {})
    assert choose_visit_kind("calm", "warmed", _rng(), dm_waiting=False) == "normal"


@pytest.mark.parametrize("seed", range(40))
def test_writes_never_come_before_the_channels(seed: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "visit_stories_first_probability", 0.5)
    monkeypatch.setattr(settings.warming, "visit_read_extra_first_probability", 0.5)
    plan = plan_visit("normal", _rng(seed))
    first_channel = min(plan.steps.index(step) for step in _CHANNEL_STEPS)
    assert plan.steps[0] == "set_online"
    assert plan.steps.index("send_dm") > first_channel
    assert len(plan.steps) == len(set(plan.steps))
    if plan.read_extra_first:
        assert plan.steps.index("extras") < first_channel
    else:
        assert plan.steps.index("extras") > plan.steps.index("send_dm")


def test_the_order_does_vary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "visit_stories_first_probability", 0.5)
    orders = {tuple(plan_visit("binge", _rng(seed)).steps) for seed in range(40)}
    assert len(orders) > 1


def test_glance_and_reply_are_short_routes() -> None:
    glance = plan_visit("glance", _rng())
    assert glance.steps == ["set_online", "join", "read", "react"]
    assert glance.channels == 1
    reply = plan_visit("reply", _rng())
    assert reply.steps == ["set_online", "send_dm"]
    assert reply.channels == 0


def test_a_binge_walks_more_channels_and_lingers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "visit_binge_linger_seconds", (240.0, 720.0))
    plan = plan_visit("binge", _rng())
    lo, hi = settings.warming.visit_binge_channels
    assert plan.channels is not None
    assert lo <= plan.channels <= hi
    assert 240.0 <= plan.linger_seconds <= 720.0


def test_kind_budgets() -> None:
    rng = _rng()
    assert {kind_budget("glance", 3, 15, rng) for _ in range(50)} == {2, 3}
    assert kind_budget("reply", 3, 15, rng) == 2
    assert kind_budget("normal", 3, 15, rng) == 3
    assert kind_budget("binge", 3, 15, rng) == 6
    assert kind_budget("binge", 3, 4, rng) == 4
    assert kind_budget("normal", None, None, rng) is None
    assert kind_budget("binge", None, None, rng) is None
    assert kind_budget("reply", None, None, rng) == 2
    assert kind_budget("glance", 3, 1, rng) == 1


@pytest.mark.asyncio
async def test_a_glance_reads_one_channel_and_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    for name in ("@chan_a", "@chan_b", "@chan_c"):
        await warming.add_channels(AddChannelsRequest(raw=name))
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    await _seed_two_warming_accounts()
    await record_dialogue_message("acc-2", "acc-1", "hola!", replied=False)

    result = await warming.run_one_cycle(
        WarmingCycleRequest(
            account_id="acc-1",
            dm_allowed=True,
            visit=plan_visit("glance", _rng()),
            remaining_actions=3,
        ),
    )

    types = recorder.types()
    assert types.count("read_channel") == 1
    assert "watch_peer_stories" not in types
    assert "send_dm" not in types
    assert result.messages_sent == 0


@pytest.mark.asyncio
async def test_a_reply_visit_only_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    # The persona's DM roll would refuse every time; a reply visit skips only that roll.
    monkeypatch.setattr(_seams.rng, "random", lambda: 0.999)

    async def gen(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text="todo bien")

    monkeypatch.setattr(_seams, "generate_text", gen)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    await _seed_two_warming_accounts()
    await record_dialogue_message("acc-2", "acc-1", "hola!", replied=False)

    result = await warming.run_one_cycle(
        WarmingCycleRequest(
            account_id="acc-1",
            dm_allowed=True,
            visit=plan_visit("reply", _rng()),
            remaining_actions=2,
        ),
    )

    types = recorder.types()
    assert "read_channel" not in types
    assert "join_channel" not in types
    assert "send_dm" in types
    assert result.messages_sent == 1


@pytest.mark.asyncio
async def test_a_reply_visit_still_obeys_the_dm_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    await _seed_two_warming_accounts()
    await record_dialogue_message("acc-2", "acc-1", "hola!", replied=False)

    result = await warming.run_one_cycle(
        WarmingCycleRequest(
            account_id="acc-1", dm_allowed=False, visit=plan_visit("reply", _rng())
        ),
    )

    assert "send_dm" not in recorder.types()
    assert result.messages_sent == 0


@pytest.mark.asyncio
async def test_stories_and_a_free_read_may_open_the_visit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings.warming, "persona_extras", dict.fromkeys(settings.warming.persona_extras, (0, 0))
    )
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_ready_account()
    await _set_settings(chat=False, reactions=False, key="")
    await save_warming_settings(
        gemini_api_key=None,
        extra_toggles={
            **dict.fromkeys(EXTRA_TOGGLE_DEFAULTS, False),
            "dialogs": True,
            "saved": True,
        },
    )
    visit = VisitPlan(
        kind="normal",
        steps=["set_online", "extras", "stories", "join", "read", "react", "send_dm"],
        stories_first=True,
        read_extra_first=True,
    )
    steps: list[str] = []

    async def _on_step(step: str) -> None:
        steps.append(step)

    await warming.run_one_cycle(
        WarmingCycleRequest(account_id="acc-1", visit=visit), on_step=_on_step
    )

    types = recorder.types()
    first_read = types.index("read_channel")
    assert types.index("warm_get_dialogs") < first_read
    assert types.index("watch_peer_stories") < first_read
    assert types.count("watch_peer_stories") == 1
    # The write extra never opens a visit.
    assert "warm_self_note" not in types[:first_read]
    assert steps[:3] == ["set_online", "extras", "stories"]


@pytest.mark.asyncio
async def test_the_progress_follows_the_visits_own_order(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[str] = []

    async def store_progress(_account_id: str, step: str, *_args: object) -> None:
        writes.append(step)

    monkeypatch.setattr(_iteration, "store_cycle_progress", store_progress)
    reporter = _iteration._ProgressReporter(
        "acc-1", None, ["set_online", "stories", "join", "read", "react", "send_dm"]
    )
    for step in ("stories", "join", "read", "join", "react", "stories", "send_dm", "extras"):
        await reporter(step)
    assert writes == ["stories", "join", "read", "react", "send_dm"]


@pytest.mark.asyncio
async def test_the_visit_is_stored_with_the_booking_for_the_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_iteration, "choose_visit_kind", lambda *_a, **_k: "glance")
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))
    plan = _iteration._IterationPlan(
        age_hours=500.0,
        effective_cap=15,
        daily=(0, "2026-06-13"),
        remaining=15,
        dm_allowed=False,
        persona="normal",
    )

    reserved = await _iteration._reserve_iteration("acc-1", plan, None)

    assert isinstance(reserved, _iteration._ReservedIteration)
    assert reserved.visit is not None
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.visit_kind == "glance"
    assert record.cycle_plan == reserved.visit.steps
    # Any later write leaves the booking's marks alone.
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="sleeping"))
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert (record.visit_kind, record.cycle_plan) == ("glance", reserved.visit.steps)


@pytest.mark.asyncio
async def test_the_iteration_caps_the_cycle_by_the_kind_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_iteration, "visit_budget", _cadence.visit_budget)
    monkeypatch.setattr(_iteration, "choose_visit_kind", lambda *_a, **_k: "glance")
    monkeypatch.setattr(_seams.rng, "randint", lambda lo, _hi: lo)
    requests: list[WarmingCycleRequest] = []

    async def cycle(data: WarmingCycleRequest, **_kwargs: object) -> WarmingCycleResult:
        requests.append(data)
        return WarmingCycleResult(account_id=data.account_id, status="ok", attempted_actions=2)

    monkeypatch.setattr(_loop, "_execute_cycle", cycle)
    await _seed_channel()
    await create_account(AccountCreate(account_id="acc-1"))
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)

    await warming.run_loop_iteration("acc-1")

    assert requests
    assert requests[0].visit is not None
    assert requests[0].visit.kind == "glance"
    assert requests[0].remaining_actions == 2
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.visit_kind == "glance"
