"""An activity session in a mobile account's pod: only a running pod, one at a time.

Reuses the scripted container of ``test_mobile_emulator``; the playbook itself is a
fake that records where it ran (its gestures are pinned in
``tests/core/emulator/test_playbook.py``).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import EmulatorError
from core.emulator.redroid import Instance
from schemas.emulator import PlaybookPlan, PlaybookResult
from services.accounts import mobile_emulator as emulator_module
from services.accounts.mobile_emulator import (
    EmulatorBusyError,
    EmulatorRefusedError,
    claim_manual,
    open_mobile_visit,
    release_manual,
    run_mobile_activity,
)
from tests.services.accounts._mobile_support import _account, _Container

pytestmark = pytest.mark.usefixtures("mobile_enabled")

if TYPE_CHECKING:
    from schemas.emulator import EmulatorRefusalCode


class _Playbook:
    """Stands in for ``TelegramMobilePlaybook``; every instance reports to the class."""

    runs: list[tuple[str, int]]
    plans: list[PlaybookPlan | None]
    started: asyncio.Event
    fail: EmulatorRefusalCode | None = None
    gate: asyncio.Event | None = None

    def __init__(self, *, progress: PlaybookResult | None = None) -> None:
        del progress
        self.result = PlaybookResult()

    async def run_session(
        self, serial: str, duration_sec: int, plan: PlaybookPlan | None = None
    ) -> PlaybookResult:
        cls = type(self)
        cls.runs.append((serial, duration_sec))
        cls.plans.append(plan)
        self.result = PlaybookResult(channels_read=1)  # what it did before any gate
        cls.started.set()
        gate, fail = cls.gate, cls.fail
        if gate is not None:
            await gate.wait()
        if fail is not None:
            raise EmulatorError(fail)
        return PlaybookResult(channels_read=1, scrolls=4)


@pytest.fixture
def playbook(monkeypatch: pytest.MonkeyPatch) -> type[_Playbook]:
    _Playbook.runs = []
    _Playbook.plans = []
    _Playbook.started = asyncio.Event()
    _Playbook.fail = None
    _Playbook.gate = None
    monkeypatch.setattr(emulator_module, "TelegramMobilePlaybook", _Playbook)
    emulator_module._ACTIVE.clear()
    emulator_module._OPERATOR.clear()
    return _Playbook


@pytest.mark.asyncio
async def test_a_running_pod_on_the_current_proxy_gets_its_session(
    container: _Container,
    playbook: type[_Playbook],
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)

    result = await run_mobile_activity("acc", 120)

    assert result == PlaybookResult(channels_read=1, scrolls=4)
    assert playbook.runs == [("127.0.0.1:5601", 120)]
    assert container.steps == [
        "find:telebuba-acc",
        "proxy?:10.0.0.5",
        "connect",
        "front",
        "home",
    ], "the app is left with HOME, as a phone put away"
    assert "acc" not in emulator_module._ACTIVE


@pytest.mark.asyncio
async def test_a_session_is_capped(
    container: _Container,
    playbook: type[_Playbook],
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)

    await run_mobile_activity("acc", 10**6)

    assert playbook.runs == [("127.0.0.1:5601", settings.emulator.activity_max_seconds)]


@pytest.mark.parametrize("state", ["missing", "stopped", "other_proxy"])
@pytest.mark.asyncio
async def test_a_pod_that_is_not_running_is_never_started(
    container: _Container,
    playbook: type[_Playbook],
    state: str,
) -> None:
    await _account()
    if state != "missing":
        container.instance = Instance(1, "telebuba-acc", running=state != "stopped")
    container.on_current_proxy = state != "other_proxy"

    with pytest.raises(EmulatorRefusedError) as refused:
        await run_mobile_activity("acc", 60)

    assert refused.value.code == "emulator_not_running"
    assert playbook.runs == []
    assert not any(step.startswith(("pod:", "connect", "boot")) for step in container.steps)


@pytest.mark.asyncio
async def test_a_failed_session_answers_its_code_and_frees_the_account(
    container: _Container,
    playbook: type[_Playbook],
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)
    playbook.fail = "emulator_not_signed_in"

    with pytest.raises(EmulatorRefusedError) as refused:
        await run_mobile_activity("acc", 60)

    assert refused.value.code == "emulator_not_signed_in"
    assert refused.value.partial == PlaybookResult(channels_read=1), "what it did goes with it"
    assert "acc" not in emulator_module._ACTIVE


@pytest.mark.asyncio
async def test_a_refusal_before_the_session_carries_nothing_it_did(
    container: _Container,
    playbook: type[_Playbook],
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)
    container.fail_at = "connect"

    with pytest.raises(EmulatorRefusedError) as refused:
        await run_mobile_activity("acc", 60)

    assert refused.value.partial is None
    assert playbook.runs == []


@pytest.mark.asyncio
async def test_a_session_past_its_time_ends_with_what_it_did(
    container: _Container,
    playbook: type[_Playbook],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)
    playbook.gate = asyncio.Event()  # never set: the session would run forever
    monkeypatch.setattr(emulator_module, "_SESSION_GRACE_SECONDS", 0.05)

    result = await run_mobile_activity("acc", 0)

    assert result == PlaybookResult(channels_read=1)
    assert "acc" not in emulator_module._ACTIVE


@pytest.mark.asyncio
async def test_no_session_while_the_operator_holds_the_pod(
    container: _Container,
    playbook: type[_Playbook],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)
    emulator_module._OPERATOR.add("acc")
    monkeypatch.setattr(settings.emulator, "desktop_viewer", True)
    monkeypatch.setattr(emulator_module, "viewer_open", lambda name: name == "telebuba-acc")

    with pytest.raises(EmulatorBusyError):
        await run_mobile_activity("acc", 60)

    assert playbook.runs == []


@pytest.mark.asyncio
async def test_one_session_at_a_time_and_none_during_a_start(
    container: _Container,
    playbook: type[_Playbook],
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)
    playbook.gate = asyncio.Event()

    first = asyncio.create_task(run_mobile_activity("acc", 60))
    await playbook.started.wait()
    with pytest.raises(EmulatorBusyError):
        await run_mobile_activity("acc", 60)
    playbook.gate.set()
    await first

    start = emulator_module._LOCKS.setdefault("acc", asyncio.Lock())
    async with start:
        with pytest.raises(EmulatorBusyError):
            await run_mobile_activity("acc", 60)
    assert len(playbook.runs) == 1


@pytest.mark.asyncio
async def test_the_feature_switch_refuses_a_session(
    container: _Container,
    playbook: type[_Playbook],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account()
    monkeypatch.setattr(settings.emulator, "enabled", False)

    with pytest.raises(EmulatorRefusedError) as refused:
        await run_mobile_activity("acc", 60)

    assert refused.value.code == "mobile_emulator_disabled"
    assert container.steps == []
    assert playbook.runs == []


@pytest.mark.asyncio
async def test_the_plan_reaches_the_playbook(
    container: _Container,
    playbook: type[_Playbook],
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)
    plan = PlaybookPlan(channels=2, reaction_probability=0.0, stories=False)

    await run_mobile_activity("acc", 60, plan)

    assert playbook.plans == [plan]


@pytest.mark.asyncio
async def test_a_failed_session_does_not_press_home(
    container: _Container,
    playbook: type[_Playbook],
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)
    playbook.fail = "emulator_field_missing"

    with pytest.raises(EmulatorRefusedError):
        await run_mobile_activity("acc", 60)

    assert "home" not in container.steps


@pytest.mark.asyncio
async def test_a_warming_visit_checks_its_pod_once(
    container: _Container,
    playbook: type[_Playbook],
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)

    serial = await open_mobile_visit("acc")
    await run_mobile_activity("acc", 60, opened=serial)

    assert playbook.runs == [("127.0.0.1:5601", 60)]
    assert container.steps == [
        "find:telebuba-acc",
        "proxy?:10.0.0.5",
        "connect",
        "battery",
        "front",
        "wait",
        "home",
    ], "the session goes on from the open's checks: no second docker ps, sha256sum or monkey"


@pytest.mark.asyncio
async def test_a_signed_out_visit_hands_no_serial(container: _Container) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)
    container.screens = ["confirm"]

    assert await open_mobile_visit("acc") is None


@pytest.mark.asyncio
async def test_a_visit_session_still_yields_to_a_hand_on_the_phone(
    container: _Container,
    playbook: type[_Playbook],
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)
    serial = await open_mobile_visit("acc")
    claim_manual("acc")
    try:
        with pytest.raises(EmulatorBusyError):
            await run_mobile_activity("acc", 60, opened=serial)
    finally:
        release_manual("acc")

    assert playbook.runs == []
