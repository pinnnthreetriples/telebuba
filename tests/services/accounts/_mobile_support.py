"""The scripted pod and Telegram app the mobile account tests share, and their helpers.

``_Container`` stands in for the container layer on the service modules' own names
(the ``container`` fixture in ``conftest`` installs it); ``_account`` seeds a mobile
account with a proxy; ``_fake_sign_in`` stands in for the Telethon second-device sign-in.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.android_apk import AppIdentity
from core.emulator import EmulatorError
from core.emulator.redroid import Instance
from core.repositories.proxies import create_and_assign_proxy, update_proxy_check
from core.telegram_client import TelegramClientPoolError
from schemas.accounts import AccountCreate
from schemas.proxy import ProxyCheckUpdate, ProxyCreateAssignment
from services.accounts import add_account
from services.accounts import devices as devices_module

if TYPE_CHECKING:
    import pytest

    from core.emulator._profile import ContainerSpec
    from core.emulator.ui_dump import Screen
    from schemas.proxy import ProxySettings

from core.device_fingerprint_android import generate_android_fingerprint
from core.repositories.device_fingerprint import record_second_device
from schemas.devices import SecondDeviceResult, SecondDeviceSignIn

_PHONE = "+5215550001111"


class _Container:
    """A scripted pod + Telegram app: records every step, shows ``screens`` in turn.

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
        self.on_current_proxy = True
        self.code: str | None = "12345"
        self.password: str | None = "cloud-pass"
        self.remove_gate: asyncio.Event | None = None
        self.removing = asyncio.Event()
        self.specs: list[ContainerSpec] = []
        self.boot_gate: asyncio.Event | None = None
        self.booting = asyncio.Event()
        self.apk_fails = False

    def step(self, name: str) -> None:
        self.steps.append(name)
        if name == self.fail_at or (
            self.fail_at_prefix is not None and name.startswith(self.fail_at_prefix)
        ):
            code = "emulator_boot_timeout"
            raise EmulatorError(code)

    def on_serial(self, serial: str, label: str) -> None:
        assert serial == "127.0.0.1:5601", "instance 1's own port, never index 0's"
        self.step(label)

    def next_screen(self) -> Screen:
        return self.screens.pop(0) if len(self.screens) > 1 else self.screens[0]

    async def find_instance(self, name: str) -> Instance | None:
        self.step(f"find:{name}")
        return self.instance

    async def start_pod(self, name: str, spec: ContainerSpec, proxy: ProxySettings) -> Instance:
        self.step(f"pod:{spec.manufacturer}{spec.model}:{spec.sdk}:{proxy.host}")
        self.specs.append(spec)
        self.instance = Instance(1, name, running=True)
        self.launched.set()
        if self.launch_gate is not None:
            await self.launch_gate.wait()
        return self.instance

    async def pod_runs_proxy(self, _name: str, proxy: ProxySettings) -> bool:
        self.step(f"proxy?:{proxy.host}")
        return self.on_current_proxy

    async def bring_app_to_front(self, serial: str) -> None:
        self.on_serial(serial, "front")

    async def leave_app(self, serial: str) -> None:
        self.on_serial(serial, "home")

    async def wait_for_boot(self, serial: str) -> None:
        self.booting.set()
        if self.boot_gate is not None:
            await self.boot_gate.wait()
        self.on_serial(serial, "boot")

    async def ensure_telegram_apk(self) -> AppIdentity:
        self.step("apk")
        if self.apk_fails:
            code = "emulator_download_failed"
            raise EmulatorError(code)
        return AppIdentity(package="org.telegram.messenger.web", version="1 (1)")

    async def connect_device(self, serial: str) -> None:
        self.on_serial(serial, "connect")

    async def show_instance(self, _name: str, index: int) -> None:
        self.step(f"show:{index}")

    async def assert_sdk(self, serial: str, sdk: int) -> None:
        self.on_serial(serial, f"sdk:{sdk}")

    async def apply_stealth_profile(self, serial: str) -> str:
        self.on_serial(serial, "stealth")
        if self.fail_at == "stealth":
            msg = "simulated stealth failure"
            raise RuntimeError(msg)
        return "fake-boot-id"

    async def ensure_app(self, serial: str, *, apk_path: object = None) -> None:
        del apk_path
        self.on_serial(serial, "app")

    async def seed_device_if_needed(self, serial: str, spec: ContainerSpec) -> bool:
        self.on_serial(serial, f"seed:{spec.serial}")
        return True

    async def read_screen(self, serial: str) -> tuple[Screen, list[object]]:
        self.on_serial(serial, "read")
        return self.next_screen(), []

    async def wait_screen(self, serial: str, _wanted: set[Screen]) -> tuple[Screen, list[object]]:
        self.on_serial(serial, "wait")
        return self.next_screen(), []

    async def stop_pod(self, name: str) -> None:
        self.step(f"stop:{name}")

    async def remove_pod(self, name: str) -> bool:
        self.removing.set()
        if self.remove_gate is not None:
            await self.remove_gate.wait()
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
        return self.password

    def ui(self, name: str):
        async def call(serial: str, _nodes: object, *values: str) -> None:
            self.on_serial(serial, f"{name}:{values[0]}" if values else name)

        return call


_SERVICE_FAKES = (
    "find_instance",
    "start_pod",
    "pod_runs_proxy",
    "wait_for_boot",
    "connect_device",
    "show_instance",
    "assert_sdk",
    "apply_stealth_profile",
    "ensure_app",
    "ensure_telegram_apk",
    "seed_device_if_needed",
    "bring_app_to_front",
    "leave_app",
    "read_screen",
    "stop_pod",
    "remove_pod",
    "fetch_account_twofa_password",
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


async def _account(
    *,
    session_device: str = "mobile",
    proxy_type: str | None = "socks5",
    proxy_country: str | None = None,
) -> None:
    await add_account(
        AccountCreate(
            account_id="acc",
            session_name="acc",
            phone=_PHONE,
            session_device=session_device,  # ty: ignore[invalid-argument-type]
        ),
    )
    if proxy_type is not None:
        proxy = await create_and_assign_proxy(
            ProxyCreateAssignment(
                proxy_type=proxy_type,  # ty: ignore[invalid-argument-type]
                host="10.0.0.5",
                port=1080,
                account_id="acc",
            ),
        )
        if proxy_country is not None:
            await update_proxy_check(
                ProxyCheckUpdate(
                    proxy_id=proxy.id, status="tcp_working", country_code=proxy_country
                ),
            )


def _fake_sign_in(
    monkeypatch: pytest.MonkeyPatch,
    code: str | None = None,
    flood: int | None = None,
) -> list[SecondDeviceSignIn]:
    calls: list[SecondDeviceSignIn] = []

    async def fake(request: SecondDeviceSignIn) -> SecondDeviceResult:
        calls.append(request)
        if code is None:
            await record_second_device(
                request.session_name,
                generate_android_fingerprint(request.account_id, _PHONE),
            )
        return SecondDeviceResult(
            account_id=request.account_id,
            device_type=request.device_type,
            code=code,  # ty: ignore[invalid-argument-type]
            flood_wait_seconds=flood,
        )

    monkeypatch.setattr(devices_module, "authorize_second_device", fake)
    return calls
