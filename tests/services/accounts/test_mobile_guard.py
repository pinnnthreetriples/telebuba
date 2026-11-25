"""What the operator's open is checked against: the pod limit, the host's memory, the barrier.

The container layer is the scripted fake (``_mobile_support``): ``others_up`` are the
other pods Docker runs, ``host_memory`` is what ``docker info`` says, ``gateway_fault`` is
what the barrier re-check finds. A pod the account already has running is not a new pod,
a pod that already exists has its gateway checked at every use, and one that fails is
stopped and never handed over.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import EmulatorError
from core.emulator.redroid import Instance
from schemas.emulator import PlaybookResult
from services.accounts import _mobile_guard
from services.accounts import mobile_emulator as emulator_module
from services.accounts.mobile_emulator import (
    EmulatorRefusedError,
    boot_account_phone,
    open_account_mobile,
    open_mobile_visit,
    run_mobile_activity,
)
from tests.services.accounts._mobile_support import _account, _Container

if TYPE_CHECKING:
    from core.emulator._errors import GatewayFault

pytestmark = pytest.mark.usefixtures("mobile_enabled")

_GIB = 1024**3
_MINE = "telebuba-acc"


def _others(count: int, *, paused: bool = False) -> list[Instance]:
    return [
        Instance(10 + n, f"telebuba-other{n}", running=not paused, paused=paused)
        for n in range(count)
    ]


@pytest.fixture
def events(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, str, str | None, dict[str, object]]]:
    logged: list[tuple[str, str, str | None, dict[str, object]]] = []

    async def fake_log(
        level: str,
        event: str,
        *,
        account_id: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        logged.append((level, event, account_id, extra or {}))

    monkeypatch.setattr(_mobile_guard, "log_event", fake_log)
    monkeypatch.setattr(emulator_module, "log_event", fake_log)
    return logged


@pytest.fixture(autouse=True)
def _pins(monkeypatch: pytest.MonkeyPatch) -> None:
    # ``EMULATOR__*`` in the environment must not decide these tests.
    monkeypatch.setattr(settings.emulator, "max_running_pods", 4)
    monkeypatch.setattr(settings.emulator, "memory_mb", 2048)
    monkeypatch.setattr(settings.emulator, "host_memory_fraction", 0.8)
    monkeypatch.setattr(settings.emulator, "gateway_check_enforce", True)
    emulator_module._OPERATOR.clear()  # an earlier test's open must not hold this one's pod


# ----------------------------------------------------------------------- pod limit


@pytest.mark.asyncio
async def test_the_operators_open_obeys_the_pod_limit_before_anything_is_started(
    container: _Container,
) -> None:
    await _account()
    container.others_up = _others(4)

    with pytest.raises(EmulatorRefusedError) as refused:
        await open_account_mobile("acc")

    assert refused.value.code == "emulator_pod_limit"
    assert not any(step.startswith(("pod:", "apk", "boot")) for step in container.steps)
    assert container.instance is None


@pytest.mark.asyncio
async def test_a_pod_below_the_limit_starts(container: _Container) -> None:
    await _account()
    container.others_up = _others(3)

    result = await open_account_mobile("acc")

    assert result.status == "started"


@pytest.mark.asyncio
async def test_frozen_pods_count_and_stopped_ones_do_not(container: _Container) -> None:
    await _account()
    container.others_up = [*_others(2), *_others(2, paused=True)]

    with pytest.raises(EmulatorRefusedError) as refused:
        await open_account_mobile("acc")
    assert refused.value.code == "emulator_pod_limit"

    container.others_up = [
        *_others(3),
        Instance(20, "telebuba-stopped", running=False),
        Instance(21, "telebuba-gone", running=False, paused=False),
    ]
    assert (await open_account_mobile("acc")).status == "started"


@pytest.mark.asyncio
async def test_the_device_farms_boot_obeys_it_too(container: _Container) -> None:
    await _account()
    container.others_up = _others(4)

    with pytest.raises(EmulatorRefusedError) as refused:
        await boot_account_phone("acc")

    assert refused.value.code == "emulator_pod_limit"


@pytest.mark.asyncio
async def test_a_pod_the_account_already_runs_opens_though_the_host_is_full(
    container: _Container,
) -> None:
    await _account()
    container.others_up = _others(3)  # three others and this one: the limit's four
    container.instance = Instance(1, _MINE, running=True)
    container.host_memory = 1 * _GIB  # nothing new would fit

    result = await open_account_mobile("acc")

    assert result.status == "already_running"
    assert container.gateway_checks == [_MINE]


@pytest.mark.asyncio
async def test_a_pod_of_the_account_that_is_stopped_is_not_counted_against_it(
    container: _Container,
) -> None:
    await _account()
    container.others_up = _others(3)
    container.instance = Instance(1, _MINE, running=False)

    assert (await open_account_mobile("acc")).status == "started"


@pytest.mark.asyncio
async def test_warmings_open_holds_its_own_slot_and_is_not_asked(container: _Container) -> None:
    await _account()
    container.others_up = _others(4)

    result = await open_account_mobile("acc", background=True)

    assert result.status == "started"


# --------------------------------------------------------------------- host memory


@pytest.mark.parametrize(
    ("others", "host", "code"),
    [
        (3, 5 * _GIB, "emulator_host_memory"),  # 4 x 2 GiB = 8 > 0.8 x 5
        (0, 2 * _GIB, "emulator_host_memory"),  # 1 x 2 GiB = 2 > 0.8 x 2
        (3, 10 * _GIB, None),  # 8 GiB is exactly 0.8 x 10: not over
        (0, 16 * _GIB, None),
    ],
)
@pytest.mark.asyncio
async def test_a_pod_the_host_cannot_carry_is_refused(
    container: _Container, others: int, host: int, code: str | None
) -> None:
    await _account()
    container.others_up = _others(others)
    container.host_memory = host

    if code is None:
        assert (await open_account_mobile("acc")).status == "started"
        return
    with pytest.raises(EmulatorRefusedError) as refused:
        await open_account_mobile("acc")
    assert refused.value.code == code
    assert container.instance is None


@pytest.mark.asyncio
async def test_a_host_whose_memory_cannot_be_read_blocks_nothing_but_the_count_still_does(
    container: _Container,
) -> None:
    await _account()
    container.host_memory = None
    container.others_up = _others(3)
    assert (await open_account_mobile("acc")).status == "started"

    container.instance = None
    container.others_up = _others(4)
    with pytest.raises(EmulatorRefusedError) as refused:
        await open_account_mobile("acc")
    assert refused.value.code == "emulator_pod_limit"


@pytest.mark.asyncio
async def test_the_fraction_and_the_per_pod_figure_are_settings(
    container: _Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _account()
    container.host_memory = 4 * _GIB
    monkeypatch.setattr(settings.emulator, "memory_mb", 2048)
    monkeypatch.setattr(settings.emulator, "host_memory_fraction", 0.4)  # 1.6 GiB < 2

    with pytest.raises(EmulatorRefusedError) as refused:
        await open_account_mobile("acc")
    assert refused.value.code == "emulator_host_memory"

    monkeypatch.setattr(settings.emulator, "host_memory_fraction", 0.55)  # 2.2 GiB >= 2
    assert (await open_account_mobile("acc")).status == "started"


# ----------------------------------------------------------------- gateway barrier


@pytest.mark.asyncio
async def test_an_existing_pods_intact_barrier_lets_it_open(container: _Container) -> None:
    await _account()
    container.instance = Instance(1, _MINE, running=True)

    result = await open_account_mobile("acc")

    assert result.status == "already_running"
    assert container.gateway_checks == [_MINE]
    assert f"stop:{_MINE}" not in container.steps


@pytest.mark.parametrize("fault", ["tunnel_down", "egress_open", "check_timeout"])
@pytest.mark.asyncio
async def test_a_broken_barrier_stops_the_pod_and_is_never_handed_over(
    container: _Container,
    events: list[tuple[str, str, str | None, dict[str, object]]],
    fault: GatewayFault,
) -> None:
    await _account()
    container.instance = Instance(1, _MINE, running=True)
    container.gateway_fault = fault

    with pytest.raises(EmulatorRefusedError) as refused:
        await open_account_mobile("acc")

    assert refused.value.code == "emulator_gateway_unhealthy"
    assert f"stop:{_MINE}" in container.steps
    assert not any(step in {"connect", "front", "app"} for step in container.steps)
    assert not any(step.startswith(("pod:", "show:")) for step in container.steps)
    assert (
        "WARNING",
        "emulator_gateway_unhealthy",
        "acc",
        {"reason": fault, "enforced": True},
    ) in events


@pytest.mark.asyncio
async def test_an_unenforced_check_only_journals_and_the_pod_is_still_used(
    container: _Container,
    events: list[tuple[str, str, str | None, dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default: until the check has been watched on real pods it stops nothing."""
    monkeypatch.setattr(settings.emulator, "gateway_check_enforce", False)
    await _account()
    container.instance = Instance(1, _MINE, running=True)
    container.gateway_fault = "route_not_tunnel"

    await open_account_mobile("acc")

    assert f"stop:{_MINE}" not in container.steps
    assert (
        "WARNING",
        "emulator_gateway_unhealthy",
        "acc",
        {"reason": "route_not_tunnel", "enforced": False},
    ) in events


def _check_cannot_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gateway container is gone or docker fails: an error that is no barrier verdict."""

    async def broken(_name: str) -> None:
        code = "emulator_not_found"
        raise EmulatorError(code)

    monkeypatch.setattr(_mobile_guard, "verify_gateway", broken)


@pytest.mark.asyncio
async def test_a_check_that_cannot_run_never_aborts_the_open_when_unenforced(
    container: _Container,
    events: list[tuple[str, str, str | None, dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.emulator, "gateway_check_enforce", False)
    await _account()
    container.instance = Instance(1, _MINE, running=True)
    _check_cannot_run(monkeypatch)

    result = await open_account_mobile("acc")

    assert result.status == "already_running"
    assert f"stop:{_MINE}" not in container.steps
    assert (
        "WARNING",
        "emulator_gateway_unhealthy",
        "acc",
        {"reason": "check_failed", "enforced": False},
    ) in events


@pytest.mark.asyncio
async def test_a_check_that_cannot_run_stops_the_pod_and_refuses_when_enforced(
    container: _Container,
    events: list[tuple[str, str, str | None, dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account()
    container.instance = Instance(1, _MINE, running=True)
    _check_cannot_run(monkeypatch)

    with pytest.raises(EmulatorRefusedError) as refused:
        await open_account_mobile("acc")

    assert refused.value.code == "emulator_gateway_unhealthy"
    assert f"stop:{_MINE}" in container.steps
    assert (
        "WARNING",
        "emulator_gateway_unhealthy",
        "acc",
        {"reason": "check_failed", "enforced": True},
    ) in events


@pytest.mark.asyncio
async def test_the_event_carries_a_reason_code_and_nothing_of_the_proxy(
    container: _Container,
    events: list[tuple[str, str, str | None, dict[str, object]]],
) -> None:
    await _account()
    container.instance = Instance(1, _MINE, running=True)
    container.gateway_fault = "kill_switch_missing"

    with pytest.raises(EmulatorRefusedError):
        await open_account_mobile("acc")

    (extra,) = [e[3] for e in events if e[1] == "emulator_gateway_unhealthy"]
    assert extra == {"reason": "kill_switch_missing", "enforced": True}
    assert "10.0.0.5" not in repr(events)


@pytest.mark.asyncio
async def test_a_parked_pod_is_checked_before_it_is_thawed_and_only_once(
    container: _Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _account()
    container.instance = Instance(1, _MINE, running=False, paused=True)
    order: list[str] = []

    async def thaw(_name: str) -> None:
        order.append("thaw")

    original = container.verify_gateway

    async def check(name: str) -> None:
        order.append("check")
        await original(name)

    monkeypatch.setattr(emulator_module, "unpause_pod", thaw)
    monkeypatch.setattr(_mobile_guard, "verify_gateway", check)

    await open_account_mobile("acc")

    assert order == ["check", "thaw"]
    assert container.gateway_checks == [_MINE]


@pytest.mark.asyncio
async def test_a_parked_pod_with_a_broken_barrier_is_not_thawed(
    container: _Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _account()
    container.instance = Instance(1, _MINE, running=False, paused=True)
    container.gateway_fault = "tunnel_down"
    thawed: list[str] = []

    async def thaw(name: str) -> None:
        thawed.append(name)

    monkeypatch.setattr(emulator_module, "unpause_pod", thaw)

    with pytest.raises(EmulatorRefusedError) as refused:
        await open_account_mobile("acc")

    assert refused.value.code == "emulator_gateway_unhealthy"
    assert thawed == []


@pytest.mark.asyncio
async def test_a_freshly_started_pod_is_not_checked_again(container: _Container) -> None:
    await _account()

    result = await open_account_mobile("acc")

    assert result.status == "started"
    assert container.gateway_checks == [], "its start already waited for the tunnel"


@pytest.mark.asyncio
async def test_a_stopped_pod_started_again_is_not_checked_again(container: _Container) -> None:
    await _account()
    container.instance = Instance(1, _MINE, running=True)
    container.on_current_proxy = False  # stopped and started on the current proxy

    result = await open_account_mobile("acc")

    assert result.status == "started"
    assert container.gateway_checks == []


@pytest.mark.asyncio
async def test_a_stop_that_fails_does_not_hide_the_refusal(
    container: _Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _account()
    container.instance = Instance(1, _MINE, running=True)
    container.gateway_fault = "tunnel_down"

    async def cannot_stop(_name: str) -> None:
        code = "emulator_command_failed"
        raise EmulatorError(code)

    monkeypatch.setattr(_mobile_guard, "stop_pod", cannot_stop)

    with pytest.raises(EmulatorRefusedError) as refused:
        await open_account_mobile("acc")

    assert refused.value.code == "emulator_gateway_unhealthy"


@pytest.mark.asyncio
async def test_warmings_open_checks_an_existing_pod_too(container: _Container) -> None:
    await _account()
    container.instance = Instance(1, _MINE, running=True)

    assert await open_mobile_visit("acc") == "127.0.0.1:5601"
    assert container.gateway_checks == [_MINE]

    container.gateway_fault = "egress_open"
    with pytest.raises(EmulatorRefusedError) as refused:
        await open_account_mobile("acc", background=True)
    assert refused.value.code == "emulator_gateway_unhealthy"


class _Playbook:
    def __init__(self, *, progress: PlaybookResult | None = None) -> None:
        del progress
        self.result = PlaybookResult()

    async def run_session(self, *_args: object) -> PlaybookResult:
        return PlaybookResult(channels_read=1)


@pytest.fixture
def playbook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(emulator_module, "TelegramMobilePlaybook", _Playbook)
    emulator_module._ACTIVE.clear()
    emulator_module._OPERATOR.clear()


@pytest.mark.usefixtures("playbook")
@pytest.mark.asyncio
async def test_a_session_checks_the_pod_it_starts_on_and_one_that_fails_is_stopped(
    container: _Container,
) -> None:
    await _account()
    container.instance = Instance(1, _MINE, running=True)

    await run_mobile_activity("acc", 60)
    assert container.gateway_checks == [_MINE]

    container.gateway_fault = "route_not_tunnel"
    with pytest.raises(EmulatorRefusedError) as refused:
        await run_mobile_activity("acc", 60)

    assert refused.value.code == "emulator_gateway_unhealthy"
    assert f"stop:{_MINE}" in container.steps
    assert "acc" not in emulator_module._ACTIVE


@pytest.mark.usefixtures("playbook")
@pytest.mark.asyncio
async def test_a_session_the_visit_just_opened_does_not_check_the_barrier_twice(
    container: _Container,
) -> None:
    await _account()
    container.instance = Instance(1, _MINE, running=True)

    serial = await open_mobile_visit("acc")
    await run_mobile_activity("acc", 60, opened=serial)

    assert container.gateway_checks == [_MINE], "the open checked it; the session trusts that"
