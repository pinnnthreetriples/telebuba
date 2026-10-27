"""A phone stint's phone is readied at its start; its visits wait for that, then heal.

The pod, host and asset calls are fakes; the job, its card state and the visit gate
are the real ones.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from core.config import settings
from core.db import create_account
from core.emulator import EmulatorError
from schemas.accounts import AccountCreate
from schemas.emulator import OpenMobileResult
from schemas.warming import PhonePrep, WarmingCycleRequest, WarmingCycleResult
from services import warming
from services.accounts.devices import DeviceRefusedError
from services.accounts.mobile_emulator import EmulatorBusyError, EmulatorRefusedError
from services.warming import _mobile_cycle, _mobile_prep, _mobile_prep_state
from services.warming._state import _set_state

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from schemas._warming_cycle import CycleStatus
    from schemas.device_fingerprint import DeviceType


class _Phone:
    """Stands in for the host check, the screen file and the pod; records each step."""

    def __init__(self) -> None:
        self.steps: list[str] = []
        self.signed_in = True
        self.error: BaseException | None = None
        self.gate: asyncio.Event | None = None

    async def check_host(self) -> None:
        self.steps.append("host")

    async def scrcpy(self) -> None:
        self.steps.append("scrcpy")

    async def apk(self) -> None:
        self.steps.append("apk")

    async def prepare_pod(self, account_id: str) -> bool:
        self.steps.append(f"pod:{account_id}")
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.signed_in


@pytest.fixture
def phone(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Phone]:
    fake = _Phone()
    monkeypatch.setattr(_mobile_prep, "check_host", fake.check_host)
    monkeypatch.setattr(_mobile_prep, "ensure_scrcpy_server", fake.scrcpy)
    monkeypatch.setattr(_mobile_prep, "ensure_telegram_apk", fake.apk)
    monkeypatch.setattr(_mobile_prep, "prepare_pod", fake.prepare_pod)
    monkeypatch.setattr(_mobile_prep_state, "STATE", {})
    monkeypatch.setattr(_mobile_prep_state, "JOBS", {})
    yield fake
    if fake.gate is not None:
        fake.gate.set()


async def _forever() -> None:
    await asyncio.Event().wait()


@pytest_asyncio.fixture
async def loop_task() -> AsyncIterator[asyncio.Task[None]]:
    """Stands in for the stint's warming loop the job is tied to."""
    task = asyncio.create_task(_forever())
    yield task
    task.cancel()


async def _start(
    loop: asyncio.Task[None], device: DeviceType = "mobile", account_id: str = "acc-1"
) -> None:
    (await _mobile_prep.prepare_phone(account_id, device))(loop)


async def _job_done(account_id: str = "acc-1") -> None:
    job = _mobile_prep_state.JOBS.get(account_id)
    if job is not None:
        await job


@pytest.mark.asyncio
async def test_a_desktop_stint_prepares_nothing(
    phone: _Phone, loop_task: asyncio.Task[None]
) -> None:
    await _start(loop_task, "desktop")

    assert phone.steps == []
    assert _mobile_prep_state.phone_prep("acc-1") is None


@pytest.mark.asyncio
async def test_a_phone_is_checked_then_readied_in_the_background(
    phone: _Phone, loop_task: asyncio.Task[None]
) -> None:
    await _start(loop_task)

    assert phone.steps == ["host"], "the start waits only for the quick host check"
    assert _mobile_prep_state.phone_prep("acc-1") == PhonePrep(status="preparing")
    await _job_done()

    assert phone.steps == ["host", "host", "scrcpy", "apk", "pod:acc-1"]
    assert _mobile_prep_state.phone_prep("acc-1") is None, "a ready phone needs no card note"


@pytest.mark.asyncio
async def test_a_host_that_cannot_run_a_phone_refuses_the_start(
    phone: _Phone, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_binder() -> None:
        code = "emulator_binder_missing"
        raise EmulatorError(code)

    monkeypatch.setattr(_mobile_prep, "check_host", no_binder)

    with pytest.raises(warming.WarmingDeviceUnavailableError) as refused:
        await _mobile_prep.prepare_phone("acc-1", "mobile")

    assert refused.value.code == "emulator_binder_missing"
    assert _mobile_prep_state.JOBS == {}
    assert phone.steps == []


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (EmulatorError("emulator_apk_missing"), "emulator_apk_missing"),
        (DeviceRefusedError("device_code_timeout"), "device_code_timeout"),
        (RuntimeError("bug"), "emulator_command_failed"),
        (None, "emulator_not_signed_in"),
    ],
    ids=["emulator", "sign-in", "unexpected", "still-signed-out"],
)
@pytest.mark.asyncio
async def test_a_failed_preparation_leaves_its_code_on_the_card(
    phone: _Phone, loop_task: asyncio.Task[None], error: BaseException | None, code: str
) -> None:
    phone.error = error
    phone.signed_in = False

    await _start(loop_task)
    await _job_done()

    assert _mobile_prep_state.phone_prep("acc-1") == PhonePrep(status="failed", code=code)
    assert _mobile_prep_state.JOBS == {}, "the job is over: visits are no longer held"


@pytest.mark.asyncio
async def test_a_phone_the_operator_holds_is_left_to_them(
    phone: _Phone, loop_task: asyncio.Task[None]
) -> None:
    phone.error = EmulatorBusyError()

    await _start(loop_task)
    await _job_done()

    assert _mobile_prep_state.phone_prep("acc-1") is None


@pytest.mark.asyncio
async def test_the_job_ends_with_the_stints_loop(
    phone: _Phone, loop_task: asyncio.Task[None]
) -> None:
    phone.gate = asyncio.Event()
    await _start(loop_task)
    job = _mobile_prep_state.JOBS["acc-1"]
    await asyncio.sleep(0)
    assert phone.steps[-1] == "pod:acc-1", "the job is inside the pod's open"

    loop_task.cancel()  # Stop, Promote or a restart's cancel-and-replace
    await asyncio.wait({job}, timeout=5)

    assert job.cancelled()
    assert _mobile_prep_state.JOBS == {}, "visits are no longer held"
    assert _mobile_prep_state.phone_prep("acc-1") is None, "no card note for a stopped stint"


@pytest.mark.asyncio
async def test_a_new_stints_job_waits_for_the_old_one_to_end(
    phone: _Phone, loop_task: asyncio.Task[None]
) -> None:
    phone.gate = asyncio.Event()
    await _start(loop_task)
    old = _mobile_prep_state.JOBS["acc-1"]
    await asyncio.sleep(0)
    new_loop = asyncio.create_task(_forever())

    await _start(new_loop)
    new = _mobile_prep_state.JOBS["acc-1"]
    await asyncio.sleep(0)

    assert new is not old
    assert phone.steps.count("pod:acc-1") == 1, "no second open while the first runs"
    loop_task.cancel()
    await asyncio.wait({old}, timeout=5)
    assert _mobile_prep_state.JOBS["acc-1"] is new, "the old job's end leaves the new one"
    assert _mobile_prep_state.phone_prep("acc-1") == PhonePrep(status="preparing")
    phone.gate.set()
    await new
    assert phone.steps.count("pod:acc-1") == 2
    assert _mobile_prep_state.phone_prep("acc-1") is None
    new_loop.cancel()


class _Visits:
    def __init__(self, status: CycleStatus = "ok") -> None:
        self.calls = 0
        self.status: CycleStatus = status

    async def __call__(
        self, request: WarmingCycleRequest, *, on_step: object, tally: object = None
    ) -> WarmingCycleResult:
        del on_step, tally
        self.calls += 1
        return WarmingCycleResult(account_id=request.account_id, status=self.status)


async def _no_step(_step: str) -> None:
    return None


@pytest.mark.asyncio
async def test_a_visit_waits_while_the_phone_is_prepared(
    phone: _Phone, monkeypatch: pytest.MonkeyPatch, loop_task: asyncio.Task[None]
) -> None:
    visits = _Visits()
    monkeypatch.setattr(_mobile_prep, "run_mobile_cycle", visits)
    phone.gate = asyncio.Event()
    await _start(loop_task)
    request = WarmingCycleRequest(account_id="acc-1")

    result = await _mobile_prep.run_visit(request, on_step=_no_step)

    assert (result.status, result.detail) == ("skipped", "emulator_preparing")
    assert visits.calls == 0
    phone.gate.set()
    await _job_done()
    result = await _mobile_prep.run_visit(request, on_step=_no_step)
    assert result.status == "ok"
    assert visits.calls == 1


@pytest.mark.parametrize(("status", "kept"), [("ok", False), ("skipped", True), ("failed", True)])
@pytest.mark.usefixtures("phone")
@pytest.mark.asyncio
async def test_only_a_visit_that_warms_clears_a_failed_preparation(
    monkeypatch: pytest.MonkeyPatch, status: CycleStatus, *, kept: bool
) -> None:
    monkeypatch.setattr(_mobile_prep, "run_mobile_cycle", _Visits(status))
    _mobile_prep_state.STATE["acc-1"] = PhonePrep(status="failed", code="emulator_code_timeout")

    await _mobile_prep.run_visit(WarmingCycleRequest(account_id="acc-1"), on_step=_no_step)

    assert (_mobile_prep_state.phone_prep("acc-1") is not None) is kept


@pytest.mark.usefixtures("phone")
@pytest.mark.asyncio
async def test_the_card_shows_the_preparation_of_a_warming_phone_stint_only() -> None:
    devices: tuple[tuple[str, DeviceType], ...] = (("acc-1", "mobile"), ("acc-2", "desktop"))
    for account_id, device in devices:
        await create_account(AccountCreate(account_id=account_id, session_name=account_id))
        await _set_state(account_id, "sleeping", device_mode=device)
        _mobile_prep_state.STATE[account_id] = PhonePrep(status="preparing")

    board = await warming.load_board()

    cards = {card.account_id: card.phone_prep for card in board.warming}
    assert cards == {"acc-1": PhonePrep(status="preparing"), "acc-2": None}


class _Pod:
    """The pod side of ``prepare_pod``: the open and the park/stop after it."""

    def __init__(self, *, signed_in: bool = True, refusal: str | None = None) -> None:
        self.calls: list[str] = []
        self.signed_in = signed_in
        self.refusal = refusal

    async def open(self, account_id: str, *, background: bool = False) -> OpenMobileResult:
        self.calls.append("open:background" if background else "open")
        if self.refusal is not None:
            raise _refused(self.refusal)
        return OpenMobileResult(account_id=account_id, status="started", signed_in=self.signed_in)

    async def park(self, _account_id: str) -> bool:
        self.calls.append("park")
        return True

    async def stop(self, _account_id: str) -> None:
        self.calls.append("stop")


@pytest.fixture
def pod_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_mobile_cycle, "_RUNNING", set())
    monkeypatch.setattr(_mobile_cycle, "_PARKED", {})
    monkeypatch.setattr(settings.emulator, "pause_ttl_seconds", 0)


def _use(pod: _Pod, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_mobile_cycle, "open_account_mobile", pod.open)
    monkeypatch.setattr(_mobile_cycle, "park_account_pod", pod.park)
    monkeypatch.setattr(_mobile_cycle, "stop_account_pod", pod.stop)


@pytest.mark.usefixtures("pod_slots")
@pytest.mark.parametrize("signed_in", [True, False])
@pytest.mark.asyncio
async def test_the_prepared_pod_gives_its_slot_back(
    monkeypatch: pytest.MonkeyPatch, *, signed_in: bool
) -> None:
    pod = _Pod(signed_in=signed_in)
    _use(pod, monkeypatch)

    assert await _mobile_cycle.prepare_pod("acc-1") is signed_in
    await asyncio.gather(*_mobile_cycle._DETACHED)

    assert pod.calls == ["open:background", "stop"], "a TTL of 0 stops even a parked pod"
    assert not _mobile_cycle._RUNNING


def _refused(code: str) -> EmulatorRefusedError:
    error = EmulatorRefusedError("emulator_not_running")
    error.code = code
    return error


@pytest.mark.usefixtures("pod_slots")
@pytest.mark.parametrize(
    ("code", "kept"), [("emulator_screen_unknown", True), ("emulator_app_unresponsive", False)]
)
@pytest.mark.asyncio
async def test_a_pod_whose_screen_could_not_be_read_is_parked_not_stopped(
    monkeypatch: pytest.MonkeyPatch, code: str, *, kept: bool
) -> None:
    # The 20:18 case: a running, signed-in pod on a screen the open did not know was
    # stopped. It may well be signed in: it is parked for the first visit to read again.
    # A frozen app is stopped: its next start relaunches it.
    monkeypatch.setattr(settings.emulator, "pause_ttl_seconds", 3600)
    pod = _Pod(refusal=code)
    _use(pod, monkeypatch)

    with pytest.raises(EmulatorRefusedError):
        await _mobile_cycle.prepare_pod("acc-1")
    for _ in range(5):
        await asyncio.sleep(0)

    assert pod.calls == ["open:background", "park" if kept else "stop"]
    assert ("acc-1" in _mobile_cycle._PARKED) is kept
    for task in list(_mobile_cycle._DETACHED):
        task.cancel()
    await asyncio.gather(*_mobile_cycle._DETACHED, return_exceptions=True)


@pytest.mark.usefixtures("pod_slots")
@pytest.mark.asyncio
async def test_the_preparation_waits_for_a_free_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    pod = _Pod()
    _use(pod, monkeypatch)
    monkeypatch.setattr(settings.emulator, "max_running_pods", 1)
    monkeypatch.setattr(_mobile_cycle, "_SLOT_WAIT_SECONDS", 0.01)
    _mobile_cycle._RUNNING.add("acc-other")

    preparing = asyncio.create_task(_mobile_cycle.prepare_pod("acc-1"))
    await asyncio.sleep(0.05)
    assert pod.calls == [], "no pod starts on a full host"

    _mobile_cycle._RUNNING.discard("acc-other")
    assert await preparing is True
    await asyncio.gather(*_mobile_cycle._DETACHED)
    assert pod.calls[0] == "open:background"


@pytest.mark.usefixtures("pod_slots")
@pytest.mark.asyncio
async def test_a_host_that_stays_full_fails_the_preparation_as_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pod = _Pod()
    _use(pod, monkeypatch)
    monkeypatch.setattr(settings.emulator, "max_running_pods", 1)
    monkeypatch.setattr(_mobile_cycle, "_SLOT_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(_mobile_cycle, "_SLOT_WAIT_LIMIT_SECONDS", 0.05)
    _mobile_cycle._RUNNING.add("acc-other")

    with pytest.raises(EmulatorRefusedError) as refused:
        await asyncio.wait_for(_mobile_cycle.prepare_pod("acc-1"), timeout=5)

    assert refused.value.code == "emulator_capacity"
    assert pod.calls == [], "no pod was started"
    assert "acc-1" not in _mobile_cycle._RUNNING, "no slot is held for the refused phone"


@pytest.mark.asyncio
async def test_a_capacity_refusal_shows_on_the_card(
    phone: _Phone, loop_task: asyncio.Task[None]
) -> None:
    phone.error = EmulatorRefusedError("emulator_capacity")

    await _start(loop_task)
    await _job_done()

    assert _mobile_prep_state.phone_prep("acc-1") == PhonePrep(
        status="failed", code="emulator_capacity"
    )
