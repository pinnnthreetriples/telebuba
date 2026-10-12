"""Opening a mobile account in its container: refusals, one start at a time, clean-up.

The container layer is replaced by a scripted fake on the service modules' own names,
so these tests pin the POLICY: what is refused before a container is touched, the 409
for a second concurrent start, the network-off first start, the code request paced and
cut off before the number is sent, the sign-in state read off the screen, and what a
failure leaves behind.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import EmulatorError
from core.emulator.ldconsole import Instance
from core.repositories.proxies import create_and_assign_proxy
from core.telegram_client import TelegramClientPoolError
from schemas.accounts import AccountCreate
from schemas.proxy import ProxyCreateAssignment
from services.accounts import _emulator_sign_in, add_account, remove_account
from services.accounts import devices as devices_module
from services.accounts import mobile_emulator as emulator_module
from services.accounts.devices import DeviceRefusedError
from services.accounts.mobile_emulator import (
    EmulatorBusyError,
    EmulatorRefusedError,
    open_account_mobile,
)

if TYPE_CHECKING:
    from core.emulator._profile import ContainerSpec
    from core.emulator.ui_dump import Screen

_PHONE = "+5215550001111"


class _Container:
    """A scripted LDPlayer + Telegram app: records every step, shows ``screens`` in turn.

    Its methods stand in for the container layer's functions on the two service modules.
    """

    def __init__(self) -> None:
        self.instance: Instance | None = None
        self.screens: list[Screen] = ["chats"]
        self.steps: list[str] = []
        self.fail_at: str | None = None
        self.fail_at_prefix: str | None = None
        self.launch_gate: asyncio.Event | None = None
        self.launched = asyncio.Event()
        self.code: str | None = "12345"

    def step(self, name: str) -> None:
        self.steps.append(name)
        if name == self.fail_at or (
            self.fail_at_prefix is not None and name.startswith(self.fail_at_prefix)
        ):
            code = "emulator_boot_timeout"
            raise EmulatorError(code)

    def on_serial(self, serial: str, label: str) -> None:
        assert serial == "emulator-5556", "instance 1's own serial, never emulator-5554"
        self.step(label)

    def next_screen(self) -> Screen:
        return self.screens.pop(0) if len(self.screens) > 1 else self.screens[0]

    async def find_instance(self, name: str) -> Instance | None:
        self.step(f"find:{name}")
        return self.instance

    async def create_instance(
        self,
        name: str,
        spec: ContainerSpec,
        template: str | None,
    ) -> Instance:
        self.step(f"create:{spec.manufacturer}{spec.model}:{spec.sdk}:{template}")
        self.instance = Instance(1, name, running=False)
        return self.instance

    async def bring_app_to_front(self, serial: str) -> None:
        self.on_serial(serial, "front")

    async def launch_instance(self, index: int) -> None:
        self.step(f"launch:{index}")
        self.launched.set()
        if self.launch_gate is not None:
            await self.launch_gate.wait()

    async def wait_for_boot(self, serial: str) -> None:
        self.on_serial(serial, "boot")

    async def assert_sdk(self, serial: str, sdk: int) -> None:
        self.on_serial(serial, f"sdk:{sdk}")

    async def set_network(self, serial: str, *, enabled: bool) -> None:
        self.on_serial(serial, f"net:{enabled}")

    async def ensure_app(self, serial: str) -> None:
        self.on_serial(serial, "app")

    async def apply_account_proxy(self, serial: str, account_id: str) -> None:
        self.on_serial(serial, f"proxy:{account_id}")

    async def read_screen(self, serial: str) -> tuple[Screen, list[object]]:
        self.on_serial(serial, "read")
        return self.next_screen(), []

    async def wait_screen(self, serial: str, _wanted: set[Screen]) -> tuple[Screen, list[object]]:
        self.on_serial(serial, "wait")
        return self.next_screen(), []

    async def quit_instance(self, index: int) -> None:
        self.step(f"quit:{index}")

    async def remove_container(self, name: str) -> bool:
        self.step(f"remove:{name}")
        return True

    async def login_code_cutoff(self, _account_id: str) -> int:
        self.step("cutoff")
        return 41

    async def wait_account_login_code(
        self,
        _account_id: str,
        *,
        after_id: int,
        **_timing: float,
    ) -> str | None:
        self.step(f"code_after:{after_id}")
        if self.code == "pool-down":
            raise TelegramClientPoolError(_account_id, ConnectionError())
        return self.code

    async def fetch_account_twofa_password(self, _account_id: str) -> str | None:
        return "cloud-pass"

    def ui(self, name: str):
        async def call(serial: str, _nodes: object, *values: str) -> None:
            self.on_serial(serial, f"{name}:{values[0]}" if values else name)

        return call


_SERVICE_FAKES = (
    "find_instance",
    "create_instance",
    "launch_instance",
    "wait_for_boot",
    "assert_sdk",
    "set_network",
    "ensure_app",
    "apply_account_proxy",
    "bring_app_to_front",
    "read_screen",
    "quit_instance",
    "remove_container",
)
_SIGN_IN_FAKES = (
    "wait_screen",
    "login_code_cutoff",
    "wait_account_login_code",
    "fetch_account_twofa_password",
)
_SCREEN_ACTIONS = {
    "press_start": "start",
    "enter_phone": "phone",
    "confirm_number": "confirm",
    "enter_code": "code",
    "enter_password": "password",
}


@pytest.fixture
def container(monkeypatch: pytest.MonkeyPatch) -> _Container:
    fake = _Container()
    for name in _SERVICE_FAKES:
        monkeypatch.setattr(emulator_module, name, getattr(fake, name))
    for name in _SIGN_IN_FAKES:
        monkeypatch.setattr(_emulator_sign_in, name, getattr(fake, name))
    for name, label in _SCREEN_ACTIONS.items():
        monkeypatch.setattr(_emulator_sign_in, name, fake.ui(label))
    return fake


@pytest.fixture(autouse=True)
def _enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.emulator, "enabled", True)
    monkeypatch.setattr(settings.emulator, "instance_prefix", "telebuba-")
    devices_module._LAST_CODE_REQUEST.clear()
    emulator_module._LOCKS.clear()


async def _account(*, session_device: str = "mobile", proxy_type: str | None = "socks5") -> None:
    await add_account(
        AccountCreate(
            account_id="acc",
            session_name="acc",
            phone=_PHONE,
            session_device=session_device,  # ty: ignore[invalid-argument-type]
        ),
    )
    if proxy_type is not None:
        await create_and_assign_proxy(
            ProxyCreateAssignment(
                proxy_type=proxy_type,  # ty: ignore[invalid-argument-type]
                host="10.0.0.5",
                port=1080,
                account_id="acc",
            ),
        )


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
async def test_a_new_container_starts_offline_until_the_apps_proxy_is_set(
    container: _Container,
) -> None:
    await _account()
    container.screens = ["chats"]

    result = await open_account_mobile("acc")

    assert result.status == "started"
    assert result.signed_in is True
    created = next(step for step in container.steps if step.startswith("create:"))
    assert created.endswith((":33:None", ":34:None"))
    order = [s for s in container.steps if s.split(":")[0] in {"net", "app", "proxy", "launch"}]
    assert order == ["launch:1", "net:False", "app", "proxy:acc", "net:True"]


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


@pytest.mark.parametrize(("screen", "signed_in"), [("chats", True), ("phone", False)])
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
    assert container.steps == ["find:telebuba-acc", "front", "read"]


@pytest.mark.asyncio
async def test_every_boot_of_an_existing_container_reapplies_the_current_proxy_offline(
    container: _Container,
) -> None:
    """Nothing about a stopped container is trusted — not even that its proxy was set."""
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=False)

    await open_account_mobile("acc")

    order = [s for s in container.steps if s.split(":")[0] in {"net", "app", "proxy", "launch"}]
    assert order == ["launch:1", "net:False", "app", "proxy:acc", "net:True"]
    assert not any(step.startswith("create:") for step in container.steps)


@pytest.mark.asyncio
async def test_a_configured_template_of_the_fingerprints_sdk_is_copied(
    container: _Container,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account()
    monkeypatch.setattr(settings.emulator, "templates", {33: "tpl-a13", 34: "tpl-a14"})

    await open_account_mobile("acc")

    created = next(step for step in container.steps if step.startswith("create:"))
    assert created.endswith((":33:tpl-a13", ":34:tpl-a14"))


@pytest.mark.asyncio
async def test_a_failed_creation_still_removes_the_half_made_container(
    container: _Container,
) -> None:
    await _account()
    container.fail_at = "create:samsungSM-S918B:33:None"
    container.fail_at_prefix = "create:"

    with pytest.raises(EmulatorRefusedError):
        await open_account_mobile("acc")

    assert container.steps[-1] == "remove:telebuba-acc"


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
    assert emulator_module._LOCKS == {}


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
async def test_a_second_concurrent_start_is_busy_and_the_lock_is_dropped_after(
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
    assert emulator_module._LOCKS == {}, "a finished start leaves no lock behind"


@pytest.mark.asyncio
async def test_a_failed_first_start_removes_the_container_it_created(
    container: _Container,
) -> None:
    await _account()
    container.fail_at = "boot"

    with pytest.raises(EmulatorRefusedError) as refused:
        await open_account_mobile("acc")

    assert refused.value.code == "emulator_boot_timeout"
    assert container.steps[-1] == "remove:telebuba-acc"
    assert emulator_module._LOCKS == {}


@pytest.mark.asyncio
async def test_a_failed_start_of_an_existing_container_stops_it_again(
    container: _Container,
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=False)
    container.code = None
    container.screens = ["phone", "code"]

    with pytest.raises(EmulatorRefusedError) as refused:
        await open_account_mobile("acc")

    assert refused.value.code == "emulator_code_timeout"
    assert container.steps[-1] == "quit:1"
    assert not any(step.startswith("remove:") for step in container.steps)


@pytest.mark.asyncio
async def test_deleting_the_account_removes_its_container(container: _Container) -> None:
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
