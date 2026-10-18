"""Opening a mobile account in its pod: refusals, one start at a time, proxy, clean-up.

The container layer is replaced by a scripted fake on the service modules' own names,
so these tests pin the POLICY: what is refused before a container is touched, the 409
for a second concurrent start, a pod always started on the current proxy and stopped
when it changes, the code request paced and cut off before the number is sent, the
sign-in state read off the screen, and what a failure leaves behind.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator.redroid import Instance
from core.repositories.proxies import update_proxy_check
from schemas.proxy import ProxyCheckUpdate, ProxyCreateAssignment
from services import proxies
from services.accounts import devices as devices_module
from services.accounts import mobile_emulator as emulator_module
from services.accounts import remove_account
from services.accounts.devices import DeviceRefusedError
from services.accounts.mobile_emulator import (
    EmulatorBusyError,
    EmulatorRefusedError,
    open_account_mobile,
)
from tests.services.accounts._mobile_support import _PHONE, _account, _Container

pytestmark = pytest.mark.usefixtures("mobile_enabled")

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from core.emulator.ui_dump import Screen


@pytest.mark.asyncio
async def test_the_feature_switch_refuses_before_anything(container: _Container) -> None:
    await _account()
    settings.emulator.enabled = False
    try:
        with pytest.raises(EmulatorRefusedError) as refused:
            await open_account_mobile("acc")
    finally:
        settings.emulator.enabled = True

    assert refused.value.code == "mobile_emulator_disabled"
    assert container.steps == []


@pytest.mark.parametrize(
    ("session_device", "proxy_type", "code"),
    [
        ("desktop", "socks5", "device_missing"),
        ("mobile", None, "emulator_proxy_missing"),
        ("mobile", "https", "emulator_proxy_unsupported"),
    ],
)
@pytest.mark.asyncio
async def test_what_cannot_work_is_refused_before_a_container_is_touched(
    container: _Container,
    session_device: str,
    proxy_type: str | None,
    code: str,
) -> None:
    await _account(session_device=session_device, proxy_type=proxy_type)

    with pytest.raises((EmulatorRefusedError, DeviceRefusedError)) as refused:
        await open_account_mobile("acc")

    assert refused.value.code == code
    assert container.steps == []


@pytest.mark.asyncio
async def test_a_new_pod_starts_on_the_accounts_proxy_before_android_is_used(
    container: _Container,
) -> None:
    await _account()
    container.screens = ["chats"]

    result = await open_account_mobile("acc")

    assert result.status == "started"
    assert result.signed_in is True
    steps = container.steps
    started = next(step for step in steps if step.startswith("pod:"))
    assert started.endswith((":33:10.0.0.5", ":34:10.0.0.5"))
    sdk = next(step for step in steps if step.startswith("sdk:"))
    seed = next(step for step in steps if step.startswith("seed:"))
    assert seed == f"seed:{container.specs[0].serial}"
    order = [steps.index(s) for s in (started, "boot", "show:1", sdk, seed, "app")]
    assert order == sorted(order), "shown once up, seeded once its SDK is checked, then the app"


@pytest.mark.asyncio
async def test_a_signed_out_app_is_signed_in_with_a_paced_code_cut_off_before_the_number(
    container: _Container,
) -> None:
    await _account()
    container.screens = ["intro", "phone", "confirm", "code", "password", "chats"]

    result = await open_account_mobile("acc")

    assert result.signed_in is True
    flow = [s for s in container.steps if s.split(":")[0] not in {"wait", "read", "find"}]
    sign_in = flow[flow.index("start") :]
    assert sign_in == [
        "start",
        "cutoff",
        f"phone:{_PHONE}",
        "confirm",
        "code_after:41",
        "code:12345",
        "password:cloud-pass",
    ]
    assert "acc" in devices_module._LAST_CODE_REQUEST, "the request is paced like a second device"


@pytest.mark.asyncio
async def test_a_code_inside_the_cooldown_is_refused(container: _Container) -> None:
    await _account()
    devices_module.claim_code_request("acc")
    container.screens = ["phone"]

    with pytest.raises(DeviceRefusedError) as refused:
        await open_account_mobile("acc")

    assert refused.value.code == "device_code_cooldown"
    assert not any(step.startswith("phone:") for step in container.steps)


@pytest.mark.parametrize(("screen", "signed_in"), [("chats", True), ("unknown", False)])
@pytest.mark.asyncio
async def test_a_running_container_is_left_alone_and_only_asked_what_it_shows(
    container: _Container,
    screen: Screen,
    signed_in: bool,  # noqa: FBT001 - a parametrised expectation
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)
    container.screens = [screen]

    result = await open_account_mobile("acc")

    assert (result.status, result.signed_in) == ("already_running", signed_in)
    assert container.steps == [
        "find:telebuba-acc",
        "proxy?:10.0.0.5",
        "connect",
        "show:1",
        "front",
        "read",
    ]


@pytest.mark.parametrize("entry", ["intro", "phone"])
@pytest.mark.asyncio
async def test_a_running_signed_out_app_is_signed_in_where_it_stands(
    container: _Container,
    entry: Screen,
) -> None:
    """Neither booted nor reconfigured again: its gateway runs the current proxy."""
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)
    # Read once, then waited for again by the sign-in; intro goes on to the phone screen.
    then: list[Screen] = ["phone", "code", "chats"] if entry == "intro" else ["code", "chats"]
    container.screens = [entry, entry, *then]

    result = await open_account_mobile("acc")

    assert (result.status, result.signed_in) == ("already_running", True)
    assert f"phone:{_PHONE}" in container.steps
    assert "code:12345" in container.steps
    booted = {"pod", "boot", "seed", "app", "stop", "remove"}
    assert not [step for step in container.steps if step.split(":")[0] in booted]


@pytest.mark.asyncio
async def test_a_running_pod_on_another_proxy_is_started_again_on_the_current_one(
    container: _Container,
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=True)
    container.on_current_proxy = False

    result = await open_account_mobile("acc")

    assert result.status == "started"
    steps = container.steps
    started = next(step for step in steps if step.startswith("pod:"))
    assert steps.index("stop:telebuba-acc") < steps.index(started) < steps.index("boot")


@pytest.mark.asyncio
async def test_a_stopped_pod_is_started_on_the_current_proxy_not_asked_which(
    container: _Container,
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=False)

    await open_account_mobile("acc")

    assert not any(step.startswith("proxy?") for step in container.steps)
    assert any(step.endswith(":10.0.0.5") for step in container.steps if step.startswith("pod:"))


@pytest.mark.asyncio
async def test_a_pod_that_fails_to_start_is_left_to_its_own_undo(
    container: _Container,
) -> None:
    await _account()
    container.fail_at_prefix = "pod:"

    with pytest.raises(EmulatorRefusedError):
        await open_account_mobile("acc")

    assert container.steps[-1].startswith("pod:")


@pytest.mark.asyncio
async def test_deletion_waits_for_a_start_in_flight(container: _Container) -> None:
    await _account()
    container.launch_gate = asyncio.Event()
    start = asyncio.create_task(open_account_mobile("acc"))
    await container.launched.wait()

    removal = asyncio.create_task(emulator_module.remove_account_container("acc"))
    await asyncio.sleep(0)
    assert not any(step.startswith("remove:") for step in container.steps)
    container.launch_gate.set()
    await start
    await removal

    assert container.steps[-1] == "remove:telebuba-acc"
    assert emulator_module._LOCKS == {}, "the deletion alone drops the lock"


@pytest.mark.asyncio
async def test_a_start_after_a_queued_deletion_waits_for_it_not_a_fresh_lock(
    container: _Container,
) -> None:
    await _account()
    container.launch_gate = asyncio.Event()
    container.remove_gate = asyncio.Event()
    first = asyncio.create_task(open_account_mobile("acc"))
    await container.launched.wait()
    removal = asyncio.create_task(emulator_module.remove_account_container("acc"))
    await asyncio.sleep(0)
    container.launch_gate.set()
    await first
    await container.removing.wait()

    with pytest.raises(EmulatorBusyError):
        await open_account_mobile("acc")
    container.remove_gate.set()
    await removal

    assert [step for step in container.steps if step.startswith("find:")] == ["find:telebuba-acc"]


@pytest.mark.asyncio
async def test_signed_in_is_what_the_screen_shows_never_a_remembered_flag(
    container: _Container,
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=False)
    container.screens = ["chats"]
    assert (await open_account_mobile("acc")).signed_in is True

    container.screens = ["unknown"]
    container.instance = Instance(1, "telebuba-acc", running=False)

    assert (await open_account_mobile("acc")).signed_in is False


@pytest.mark.asyncio
async def test_a_second_concurrent_start_is_busy_and_the_lock_is_freed_after(
    container: _Container,
) -> None:
    await _account()
    container.launch_gate = asyncio.Event()
    first = asyncio.create_task(open_account_mobile("acc"))
    await container.launched.wait()

    with pytest.raises(EmulatorBusyError) as busy:
        await open_account_mobile("acc")
    container.launch_gate.set()
    await first

    assert busy.value.code == "emulator_busy"
    assert not emulator_module._LOCKS["acc"].locked(), "a finished start frees its lock"


@pytest.mark.asyncio
async def test_a_failed_first_start_removes_the_pod_it_created(
    container: _Container,
) -> None:
    await _account()
    container.fail_at = "boot"

    with pytest.raises(EmulatorRefusedError) as refused:
        await open_account_mobile("acc")

    assert refused.value.code == "emulator_boot_timeout"
    assert container.steps[-1] == "remove:telebuba-acc"
    assert not emulator_module._LOCKS["acc"].locked()


@pytest.mark.asyncio
async def test_a_failed_start_of_an_existing_pod_stops_it_again(
    container: _Container,
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=False)
    container.code = None
    container.screens = ["phone", "code"]

    with pytest.raises(EmulatorRefusedError) as refused:
        await open_account_mobile("acc")

    assert refused.value.code == "emulator_code_timeout"
    assert container.steps[-1] == "stop:telebuba-acc"
    assert not any(step.startswith("remove:") for step in container.steps)


@pytest.mark.asyncio
async def test_deleting_the_account_removes_its_pod(container: _Container) -> None:
    await _account()

    await remove_account("acc")

    assert "remove:telebuba-acc" in container.steps


@pytest.mark.asyncio
async def test_an_unusable_existing_session_while_waiting_is_a_refusal_not_a_500(
    container: _Container,
) -> None:
    await _account()
    container.code = "pool-down"
    container.screens = ["phone", "code"]

    with pytest.raises(DeviceRefusedError) as refused:
        await open_account_mobile("acc")

    assert refused.value.code == "device_primary_unavailable"
    assert container.steps[-1] == "remove:telebuba-acc"


@pytest.mark.parametrize("password", ["50%off", "пароль"])
@pytest.mark.asyncio
async def test_a_2fa_password_the_app_cannot_type_is_refused_before_the_number_is_sent(
    container: _Container,
    password: str,
) -> None:
    await _account()
    container.password = password
    container.screens = ["phone", "code", "password", "chats"]

    with pytest.raises(EmulatorRefusedError) as refused:
        await open_account_mobile("acc")

    assert refused.value.code == "emulator_text_unsupported"
    assert container.steps == []
    assert "acc" not in devices_module._LAST_CODE_REQUEST, "no login code was requested"


@pytest.mark.parametrize(
    "change",
    [
        lambda: proxies.unassign_proxy("acc"),
        lambda: proxies.assign_proxy_by_endpoint(
            ProxyCreateAssignment(proxy_type="socks5", host="10.0.0.9", port=1080, account_id="acc")
        ),
    ],
)
@pytest.mark.asyncio
async def test_a_proxy_change_stops_the_accounts_pod(
    container: _Container,
    change: Callable[[], Awaitable[object]],
) -> None:
    await _account()

    await change()

    assert container.steps == ["stop:telebuba-acc"]


@pytest.mark.asyncio
async def test_removing_a_proxy_stops_the_pods_of_its_accounts(container: _Container) -> None:
    await _account()
    pool = await proxies.list_pool()

    await proxies.remove_proxy(pool.proxies[0].id)

    assert container.steps == ["stop:telebuba-acc"]


@pytest.mark.asyncio
async def test_a_proxy_change_waits_for_a_start_in_flight(container: _Container) -> None:
    await _account()
    container.launch_gate = asyncio.Event()
    start = asyncio.create_task(open_account_mobile("acc"))
    await container.launched.wait()

    stop = asyncio.create_task(emulator_module.stop_account_pod("acc"))
    await asyncio.sleep(0)
    assert "stop:telebuba-acc" not in container.steps
    container.launch_gate.set()
    await start
    await stop

    assert container.steps[-1] == "stop:telebuba-acc"


@pytest.mark.asyncio
async def test_a_pod_that_cannot_be_stopped_does_not_fail_the_proxy_change(
    container: _Container,
) -> None:
    await _account()
    container.fail_at = "stop:telebuba-acc"

    await proxies.unassign_proxy("acc")

    assert container.steps == ["stop:telebuba-acc"]


@pytest.mark.asyncio
async def test_with_the_feature_off_a_proxy_change_touches_no_pod(container: _Container) -> None:
    await _account()
    settings.emulator.enabled = False
    try:
        await proxies.unassign_proxy("acc")
    finally:
        settings.emulator.enabled = True

    assert container.steps == []


@pytest.mark.asyncio
async def test_the_container_takes_the_proxy_countrys_timezone_and_carrier(
    container: _Container,
) -> None:
    await _account(proxy_country="DE")

    await open_account_mobile("acc")

    (spec,) = container.specs
    assert spec.region.timezone == "Europe/Berlin"
    assert spec.region.country == "de"
    assert spec.region.carrier is not None
    assert spec.region.carrier.mcc == "262"


@pytest.mark.asyncio
async def test_without_a_proxy_country_the_phone_numbers_country_decides(
    container: _Container,
) -> None:
    await _account()  # +52: Mexico

    await open_account_mobile("acc")

    (spec,) = container.specs
    assert (spec.region.country, spec.region.timezone) == ("mx", "America/Mexico_City")


@pytest.mark.asyncio
async def test_a_proxy_that_moved_country_moves_a_stopped_containers_region(
    container: _Container,
) -> None:
    await _account(proxy_country="DE")
    container.instance = Instance(1, "telebuba-acc", running=False)
    await open_account_mobile("acc")
    proxy_id = (await emulator_module.require_account("acc")).proxy_id
    assert proxy_id is not None
    await update_proxy_check(
        ProxyCheckUpdate(proxy_id=proxy_id, status="tcp_working", country_code="PL"),
    )
    container.instance = Instance(1, "telebuba-acc", running=False)

    await open_account_mobile("acc")

    first, second = container.specs
    assert (first.region.timezone, second.region.timezone) == ("Europe/Berlin", "Europe/Warsaw")
    assert first.serial == second.serial, "the same phone"
