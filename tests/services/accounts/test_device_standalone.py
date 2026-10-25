"""Independent emulators: phones bound to no account, each on a pool proxy of its own.

The pod is the scripted ``_Container`` on ``_standalone``'s and ``_mobile_start``'s own
names; the emulators and the proxies are real rows.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.emulator.redroid import Instance
from core.repositories.proxies import create_proxy, fetch_proxy, update_proxy_check
from core.repositories.standalone_emulators import fetch_standalone_emulator
from schemas.proxy import ProxyCheckUpdate, ProxyCreate
from schemas.standalone_emulator import StandaloneEmulatorCreate
from services import proxies
from services.accounts.mobile_emulator import EmulatorBusyError, EmulatorRefusedError
from services.device_farm import (
    _standalone,
    create_standalone_phone,
    delete_standalone_phone,
    list_phones,
    screen_session,
    set_standalone_phone_proxy,
    start_phone,
    stop_phone,
)

if TYPE_CHECKING:
    from schemas.proxy import ProxySettings
    from tests.services.accounts._mobile_support import _Container

pytestmark = pytest.mark.usefixtures("mobile_enabled")

_STANDALONE_FAKES = (
    "find_instance",
    "start_pod",
    "pod_runs_proxy",
    "connect_device",
    "assert_sdk",
    "apply_stealth_profile",
    "ensure_app",
    "ensure_telegram_apk",
    "seed_device_if_needed",
    "stop_pod",
    "remove_pod",
)


@pytest.fixture
def pod(monkeypatch: pytest.MonkeyPatch, container: _Container) -> _Container:
    """The scripted pod on ``_standalone``'s names too (``container`` covers the rest)."""
    for name in _STANDALONE_FAKES:
        monkeypatch.setattr(_standalone, name, getattr(container, name))

    async def ensure_gosuslugi(_serial: str, proxy: ProxySettings) -> None:
        container.steps.append(f"gosuslugi:{proxy.host}")

    monkeypatch.setattr(_standalone, "ensure_gosuslugi", ensure_gosuslugi)
    _standalone._LOCKS.clear()
    _standalone._MANUAL.clear()
    return container


async def _proxy(proxy_type: str = "socks5", country: str | None = "DE") -> str:
    proxy = await create_proxy(
        ProxyCreate(proxy_type=proxy_type, host="10.0.0.9", port=1080),  # ty: ignore[invalid-argument-type]
    )
    if country is not None:
        await update_proxy_check(
            ProxyCheckUpdate(proxy_id=proxy.id, status="tcp_working", country_code=country),
        )
    return proxy.id


async def _started(phone_id: str) -> None:
    await start_phone(phone_id)
    await asyncio.gather(*_standalone._STARTS)


@pytest.mark.asyncio
@pytest.mark.usefixtures("pod")
async def test_a_new_emulator_is_an_asleep_phone_with_no_app_on_its_proxy() -> None:
    proxy_id = await _proxy()

    phone = await create_standalone_phone(
        StandaloneEmulatorCreate(label="  Германия 1 ", proxy_id=proxy_id)
    )
    [listed] = (await list_phones()).phones

    assert listed == phone
    assert phone.phone_id.startswith("telebuba_sa-")
    assert (phone.kind, phone.label, phone.created, phone.status) == (
        "standalone",
        "Германия 1",
        False,
        "asleep",
    )
    assert phone.apps == []
    assert phone.install_apps == ["telegram"]
    assert phone.proxy_id == proxy_id
    assert phone.proxy is not None
    assert phone.proxy.country_code == "DE"


@pytest.mark.asyncio
async def test_an_emulator_takes_a_slot_on_its_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import settings  # noqa: PLC0415

    monkeypatch.setattr(settings.proxy, "max_accounts_per_proxy", 1)
    proxy_id = await _proxy()

    await create_standalone_phone(StandaloneEmulatorCreate(proxy_id=proxy_id))
    proxy = await fetch_proxy(proxy_id)

    assert proxy is not None
    assert (proxy.used, proxy.free) == (1, 0)
    with pytest.raises(proxies.ProxyCapacityError):
        await create_standalone_phone(StandaloneEmulatorCreate(proxy_id=proxy_id))


@pytest.mark.asyncio
async def test_an_unknown_proxy_is_refused_and_nothing_is_stored() -> None:
    with pytest.raises(ValueError, match="Proxy not found"):
        await create_standalone_phone(StandaloneEmulatorCreate(proxy_id="nope"))

    assert (await list_phones()).phones == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("pod")
async def test_a_start_without_a_proxy_is_refused_before_any_container() -> None:
    phone = await create_standalone_phone(StandaloneEmulatorCreate())

    await _started(phone.phone_id)

    with pytest.raises(EmulatorRefusedError) as refused:
        await _standalone.open_standalone(phone.phone_id.removeprefix("telebuba_sa-"))
    assert refused.value.code == "emulator_proxy_missing"


@pytest.mark.asyncio
@pytest.mark.usefixtures("pod")
async def test_an_https_proxy_cannot_carry_the_pod() -> None:
    phone = await create_standalone_phone(
        StandaloneEmulatorCreate(proxy_id=await _proxy(proxy_type="https"))
    )

    with pytest.raises(EmulatorRefusedError) as refused:
        await _standalone.open_standalone(phone.phone_id.removeprefix("telebuba_sa-"))

    assert refused.value.code == "emulator_proxy_unsupported"


@pytest.mark.asyncio
async def test_a_start_boots_the_pod_on_its_proxy_and_signs_nothing_in(pod: _Container) -> None:
    phone = await create_standalone_phone(StandaloneEmulatorCreate(proxy_id=await _proxy()))

    await _started(phone.phone_id)

    name = phone.phone_id
    assert pod.steps[0] == f"find:{name}"
    assert pod.steps[1].endswith(":10.0.0.9")
    assert {"apk", "boot", "stealth", "battery", "app"} <= set(pod.steps)
    assert not [step for step in pod.steps if step.startswith(("code", "phone", "read"))]
    [spec] = pod.specs
    assert spec.region.timezone.startswith("Europe/")
    [listed] = (await list_phones()).phones
    assert listed.status == "asleep"  # the fake lists no containers; the start is done


@pytest.mark.asyncio
async def test_a_running_pod_on_its_proxy_is_used_as_it_stands(pod: _Container) -> None:
    phone = await create_standalone_phone(StandaloneEmulatorCreate(proxy_id=await _proxy()))
    pod.instance = Instance(1, phone.phone_id, running=True)

    await _started(phone.phone_id)

    assert not [step for step in pod.steps if step.startswith("pod:")]
    assert "connect" in pod.steps


@pytest.mark.asyncio
async def test_a_failed_new_boot_removes_the_pod(pod: _Container) -> None:
    phone = await create_standalone_phone(StandaloneEmulatorCreate(proxy_id=await _proxy()))
    pod.fail_at = "boot"

    with pytest.raises(EmulatorRefusedError) as refused:
        await _standalone.open_standalone(phone.phone_id.removeprefix("telebuba_sa-"))

    assert refused.value.code == "emulator_boot_timeout"
    assert pod.steps[-1] == f"remove:{phone.phone_id}"


@pytest.mark.asyncio
async def test_a_phone_that_starts_is_busy_for_another_start_and_a_stop(pod: _Container) -> None:
    phone = await create_standalone_phone(StandaloneEmulatorCreate(proxy_id=await _proxy()))
    pod.launch_gate = asyncio.Event()

    await start_phone(phone.phone_id)
    await pod.launched.wait()

    [listed] = (await list_phones()).phones
    assert listed.status == "starting"
    with pytest.raises(EmulatorBusyError):
        await start_phone(phone.phone_id)
    with pytest.raises(EmulatorBusyError):
        await stop_phone(phone.phone_id)
    pod.launch_gate.set()
    await asyncio.gather(*_standalone._STARTS)


@pytest.mark.asyncio
async def test_a_stop_stops_the_pod(pod: _Container) -> None:
    phone = await create_standalone_phone(StandaloneEmulatorCreate(proxy_id=await _proxy()))

    result = await stop_phone(phone.phone_id)

    assert result.status == "asleep"
    assert pod.steps == [f"stop:{phone.phone_id}"]


@pytest.mark.asyncio
async def test_a_proxy_change_moves_the_phone_and_stops_its_pod(pod: _Container) -> None:
    phone = await create_standalone_phone(StandaloneEmulatorCreate())
    proxy_id = await _proxy()

    await set_standalone_phone_proxy(phone.phone_id, proxy_id)
    moved = (await list_phones()).phones[0].proxy_id
    await set_standalone_phone_proxy(phone.phone_id, None)

    assert moved == proxy_id
    assert (await list_phones()).phones[0].proxy_id is None
    assert pod.steps == [f"stop:{phone.phone_id}"] * 2


@pytest.mark.asyncio
async def test_deleting_its_proxy_takes_the_phone_off_it_and_stops_its_pod(
    pod: _Container,
) -> None:
    proxy_id = await _proxy()
    phone = await create_standalone_phone(StandaloneEmulatorCreate(proxy_id=proxy_id))

    await proxies.remove_proxy(proxy_id)

    [listed] = (await list_phones()).phones
    assert (listed.proxy_id, listed.proxy) == (None, None)
    assert pod.steps == [f"stop:{phone.phone_id}"]


@pytest.mark.asyncio
async def test_deleting_the_phone_removes_its_pod_and_frees_its_slot(pod: _Container) -> None:
    proxy_id = await _proxy()
    phone = await create_standalone_phone(StandaloneEmulatorCreate(proxy_id=proxy_id))

    await delete_standalone_phone(phone.phone_id)

    assert pod.steps == [f"remove:{phone.phone_id}"]
    assert (await list_phones()).phones == []
    proxy = await fetch_proxy(proxy_id)
    assert proxy is not None
    assert proxy.used == 0
    with pytest.raises(EmulatorRefusedError) as refused:
        await delete_standalone_phone(phone.phone_id)
    assert refused.value.code == "emulator_not_found"


@pytest.mark.asyncio
@pytest.mark.usefixtures("pod")
async def test_an_account_phone_id_is_no_standalone_one() -> None:
    with pytest.raises(EmulatorRefusedError) as refused:
        await delete_standalone_phone("telebuba-acc")

    assert refused.value.code == "emulator_not_found"
    assert _standalone.standalone_of("telebuba-acc") is None
    assert _standalone.standalone_of("telebuba_sa-") is None


@pytest.mark.asyncio
async def test_the_screen_of_a_stopped_phone_does_not_open(pod: _Container) -> None:
    phone = await create_standalone_phone(StandaloneEmulatorCreate(proxy_id=await _proxy()))
    pod.instance = Instance(1, phone.phone_id, running=False)

    with pytest.raises(EmulatorRefusedError) as refused:
        async with screen_session(phone.phone_id, "view"):
            pass

    assert refused.value.code == "emulator_not_running"


@pytest.mark.asyncio
async def test_holding_the_controls_excludes_a_start() -> None:
    phone = await create_standalone_phone(StandaloneEmulatorCreate())
    emulator_id = phone.phone_id.removeprefix("telebuba_sa-")

    _standalone.claim_standalone(emulator_id)
    try:
        assert (await list_phones()).phones[0].status == "manual"
        with pytest.raises(EmulatorBusyError):
            await start_phone(phone.phone_id)
        with pytest.raises(EmulatorBusyError):
            _standalone.claim_standalone(emulator_id)
    finally:
        _standalone.release_standalone(emulator_id)
    assert await fetch_standalone_emulator(emulator_id) is not None


@pytest.mark.asyncio
async def test_a_start_installs_only_the_apps_picked_for_the_phone(pod: _Container) -> None:
    phone = await create_standalone_phone(
        StandaloneEmulatorCreate(proxy_id=await _proxy(), install_apps=["gosuslugi"])
    )

    await _started(phone.phone_id)

    assert phone.install_apps == ["gosuslugi"]
    assert "gosuslugi:10.0.0.9" in pod.steps
    assert not {"apk", "app"} & set(pod.steps), "no Telegram was picked"


@pytest.mark.asyncio
async def test_both_apps_picked_are_both_installed(pod: _Container) -> None:
    phone = await create_standalone_phone(
        StandaloneEmulatorCreate(proxy_id=await _proxy(), install_apps=["telegram", "gosuslugi"])
    )

    await _started(phone.phone_id)

    assert {"apk", "gosuslugi:10.0.0.9", "app"} <= set(pod.steps)
    [listed] = (await list_phones()).phones
    assert listed.install_apps == ["telegram", "gosuslugi"]


@pytest.mark.asyncio
async def test_a_phone_with_no_apps_boots_bare_and_a_failed_new_boot_still_removes_it(
    pod: _Container,
) -> None:
    phone = await create_standalone_phone(
        StandaloneEmulatorCreate(proxy_id=await _proxy(), install_apps=[])
    )
    emulator_id = phone.phone_id.removeprefix("telebuba_sa-")

    await _standalone.open_standalone(emulator_id)
    bare = list(pod.steps)
    pod.steps.clear()
    pod.instance = None
    pod.fail_at = "boot"
    with pytest.raises(EmulatorRefusedError):
        await _standalone.open_standalone(emulator_id)

    assert {"boot", "battery"} <= set(bare)
    assert not [s for s in bare if s in {"apk", "app"} or s.startswith("gosuslugi")]
    assert pod.steps[-1] == f"remove:{phone.phone_id}"
