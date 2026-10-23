"""A new container's first start: its APK fetched alongside the boot, its data kept after it."""

from __future__ import annotations

import asyncio

import pytest

from core.emulator.redroid import Instance
from services.accounts.mobile_emulator import EmulatorRefusedError, open_account_mobile
from tests.services.accounts._mobile_support import _account, _Container

pytestmark = pytest.mark.usefixtures("mobile_enabled")


@pytest.mark.asyncio
async def test_a_new_containers_apk_is_fetched_while_its_pod_starts(
    container: _Container,
) -> None:
    await _account()

    await open_account_mobile("acc")

    steps = container.steps
    assert steps.count("apk") == 1
    assert steps.index("apk") < steps.index("boot") < steps.index("app"), "not after the boot"


@pytest.mark.asyncio
async def test_an_existing_container_reads_its_app_off_the_phone_and_fetches_nothing(
    container: _Container,
) -> None:
    await _account()
    container.instance = Instance(1, "telebuba-acc", running=False)

    await open_account_mobile("acc")

    assert "apk" not in container.steps


@pytest.mark.asyncio
async def test_an_apk_that_cannot_be_had_fails_a_new_pod_during_its_boot(
    container: _Container,
) -> None:
    await _account()
    container.apk_fails = True
    container.boot_gate = asyncio.Event()  # never set: the boot would take forever

    with pytest.raises(EmulatorRefusedError) as refused:
        await asyncio.wait_for(open_account_mobile("acc"), timeout=5)

    assert refused.value.code == "emulator_download_failed"
    assert "boot" not in container.steps
    assert not any(step.startswith("seed:") for step in container.steps)
    assert container.steps[-1] == "remove:telebuba-acc", "never booted: nothing to keep"


@pytest.mark.asyncio
async def test_a_new_pod_on_data_kept_from_before_is_stopped_when_its_boot_fails(
    container: _Container,
) -> None:
    await _account()
    container.apk_fails = True
    container.data_kept = True  # its Android went, its shared /data (and sign-in) stayed

    with pytest.raises(EmulatorRefusedError):
        await open_account_mobile("acc")

    assert container.steps[-1] == "stop:telebuba-acc", "the account's sign-in stays on disk"
    assert not any(step.startswith("remove:") for step in container.steps)


@pytest.mark.asyncio
async def test_a_new_pod_that_fails_after_its_boot_is_stopped_not_removed(
    container: _Container,
) -> None:
    await _account()
    container.fail_at = "app"

    with pytest.raises(EmulatorRefusedError) as refused:
        await open_account_mobile("acc")

    assert refused.value.code == "emulator_boot_timeout"
    assert "boot" in container.steps
    assert container.steps[-1] == "stop:telebuba-acc", "its seeded data spares the next start"
    assert not any(step.startswith("remove:") for step in container.steps)


@pytest.mark.asyncio
async def test_a_new_pod_with_the_wrong_sdk_is_removed(container: _Container) -> None:
    await _account()
    container.fail_at_prefix = "sdk:"

    with pytest.raises(EmulatorRefusedError):
        await open_account_mobile("acc")

    assert container.steps[-1] == "remove:telebuba-acc"


@pytest.mark.asyncio
async def test_a_new_pod_that_fails_at_stealth_is_removed(container: _Container) -> None:
    await _account()
    container.fail_at = "stealth"

    with pytest.raises(EmulatorRefusedError):
        await open_account_mobile("acc")

    assert container.steps[-1] == "remove:telebuba-acc"
