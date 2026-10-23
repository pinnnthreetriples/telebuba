"""What a phone visit is planned to do: only what the app can, under the operator's switches."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import create_account
from schemas._warming_extras import EXTRA_TOGGLE_DEFAULTS
from schemas._warming_visits import VisitPlan
from schemas.accounts import AccountCreate
from schemas.warming import WarmingCycleRequest
from services.warming import _iteration, _mobile_cycle, _mobile_plan, _seams
from services.warming._iteration import _IterationPlan

if TYPE_CHECKING:
    from schemas._warming_extras import ExtraToggles
    from schemas._warming_visits import VisitKind
    from schemas.device_fingerprint import DeviceType


class _Controls:
    def __init__(self, **toggles: bool) -> None:
        self.reactions_enabled = True
        self.extra_toggles: ExtraToggles = {**EXTRA_TOGGLE_DEFAULTS, **toggles}  # type: ignore[typeddict-item]


def _controls(monkeypatch: pytest.MonkeyPatch, **toggles: bool) -> None:
    async def load() -> _Controls:
        return _Controls(**toggles)

    monkeypatch.setattr(_mobile_plan, "load_warming_settings", load)


async def _plan_on(device: DeviceType, monkeypatch: pytest.MonkeyPatch) -> VisitPlan:
    """The visit an iteration bound to ``device`` draws while a partner's DM waits."""

    async def waiting(_account_id: str) -> object:
        return object()

    monkeypatch.setattr(_iteration, "oldest_unreplied_for", waiting)
    monkeypatch.setitem(settings.warming.persona_visit_weights, "normal", {"normal": 0.0})
    plan = _IterationPlan(30.0, 20, (0, "2026-10-23"), 20, dm_allowed=True, persona="normal")

    async def body() -> VisitPlan:
        _seams.bind_device(device)
        visit, _budget = await _iteration._plan_this_visit("acc-1", plan)
        return visit

    return await asyncio.create_task(body())


@pytest.mark.asyncio
async def test_a_phone_never_draws_a_reply_only_visit(monkeypatch: pytest.MonkeyPatch) -> None:
    assert (await _plan_on("desktop", monkeypatch)).kind == "reply"
    assert (await _plan_on("mobile", monkeypatch)).kind == "normal"


@pytest.mark.asyncio
async def test_a_phone_visit_shows_only_the_steps_the_app_takes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steps = (await _plan_on("mobile", monkeypatch)).steps

    assert set(steps) <= {"set_online", "stories", "read", "react"}
    assert steps[0] == "set_online"
    assert "read" in steps


@pytest.mark.asyncio
async def test_a_glance_on_the_phone_skips_the_stories_like_on_desktop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _controls(monkeypatch)
    request = WarmingCycleRequest(account_id="acc-1", visit=VisitPlan(kind="glance", channels=1))

    assert not (await _mobile_cycle.session_plan(request)).stories


@pytest.mark.asyncio
@pytest.mark.parametrize(("search", "queries"), [(True, ["news"]), (False, [])])
async def test_the_search_switch_gates_the_phones_search(
    monkeypatch: pytest.MonkeyPatch, *, search: bool, queries: list[str]
) -> None:
    _controls(monkeypatch, search_messages=search)
    monkeypatch.setattr(settings.emulator, "search_queries", ["news"])
    monkeypatch.setattr(_seams.rng, "random", lambda: 0.0)
    request = WarmingCycleRequest(account_id="acc-1", visit=VisitPlan(kind="normal"))

    assert (await _mobile_cycle.session_plan(request)).search_queries == queries


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("video", "voice", "notes"),
    [(False, False, False), (True, False, True), (False, True, True)],
)
async def test_the_video_and_voice_switches_gate_playing_notes_never_photos(
    monkeypatch: pytest.MonkeyPatch, *, video: bool, voice: bool, notes: bool
) -> None:
    _controls(monkeypatch, video=video, voice=voice)
    kind: VisitKind = "normal"
    request = WarmingCycleRequest(account_id="acc-1", visit=VisitPlan(kind=kind))

    plan = await _mobile_cycle.session_plan(request)

    assert plan.media, "photos stay: no switch covers them"
    assert plan.notes is notes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phone", "query"), [("+4915112345678", "wetter"), ("+79161234567", "news")]
)
async def test_the_phone_searches_in_its_own_language_where_it_can_type_it(
    monkeypatch: pytest.MonkeyPatch, phone: str, query: str
) -> None:
    """A German phone searches in German; Russian cannot be typed, so it falls back to English."""
    await create_account(AccountCreate(account_id="acc-1", phone=phone))
    _controls(monkeypatch)
    monkeypatch.setattr(settings.emulator, "search_queries", ["news"])
    monkeypatch.setattr(settings.emulator, "search_queries_by_language", {"de": ["wetter"]})
    monkeypatch.setattr(_seams.rng, "random", lambda: 0.0)
    request = WarmingCycleRequest(account_id="acc-1", visit=VisitPlan(kind="normal"))

    assert (await _mobile_cycle.session_plan(request)).search_queries == [query]
