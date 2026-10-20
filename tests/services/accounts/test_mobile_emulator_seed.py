"""A container's first-boot seeding inside the open: it never decides whether the open works."""

from __future__ import annotations

import asyncio

import pytest

from core.emulator import EmulatorError
from services.accounts import mobile_emulator as emulator_module
from services.accounts.mobile_emulator import EmulatorRefusedError, open_account_mobile
from tests.services.accounts._mobile_support import _account, _Container

pytestmark = pytest.mark.usefixtures("mobile_enabled")


@pytest.mark.asyncio
async def test_a_failed_seeding_is_logged_and_the_sign_in_goes_on(
    container: _Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _account()
    container.screens = ["chats"]
    container.fail_at_prefix = "seed:"
    events: list[tuple[str, dict[str, object]]] = []

    async def fake_log(
        _level: str,
        event: str,
        account_id: str | None = None,  # noqa: ARG001
        extra: dict[str, object] | None = None,
    ) -> None:
        events.append((event, extra or {}))

    monkeypatch.setattr(emulator_module, "log_event", fake_log)

    result = await open_account_mobile("acc")

    assert (result.status, result.signed_in) == ("started", True)
    assert "app" in container.steps
    assert not [step for step in container.steps if step.startswith(("stop:", "remove:"))]
    assert ("account_mobile_seed_failed", {"reason": "emulator_boot_timeout"}) in events
    assert ("account_mobile_opened", {}) in events


@pytest.mark.asyncio
async def test_a_seeding_that_breaks_outside_adb_is_logged_and_the_sign_in_goes_on(
    container: _Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _account()
    container.screens = ["chats"]
    events: list[tuple[str, dict[str, object]]] = []

    async def broken(_serial: str, _spec: object) -> bool:
        message = "No space left on device"  # the host's temp dir, not the phone
        raise OSError(message)

    async def fake_log(
        _level: str,
        event: str,
        account_id: str | None = None,  # noqa: ARG001
        extra: dict[str, object] | None = None,
    ) -> None:
        events.append((event, extra or {}))

    monkeypatch.setattr(emulator_module, "seed_device_if_needed", broken)
    monkeypatch.setattr(emulator_module, "log_event", fake_log)

    result = await open_account_mobile("acc")

    assert (result.status, result.signed_in) == ("started", True)
    assert ("account_mobile_seed_failed", {"reason": "OSError"}) in events


@pytest.mark.asyncio
async def test_the_seeding_runs_while_the_app_starts_and_the_open_waits_for_it(
    container: _Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _account()
    container.screens = ["chats"]
    app_started = asyncio.Event()

    async def ensure_app(serial: str) -> None:
        await container.ensure_app(serial)
        app_started.set()

    async def seed(_serial: str, _spec: object) -> bool:
        # One after the other, the app would wait for this seeding: it never ends.
        await asyncio.wait_for(app_started.wait(), timeout=10)
        await asyncio.sleep(0)
        container.step("seeded")
        return True

    monkeypatch.setattr(emulator_module, "ensure_app", ensure_app)
    monkeypatch.setattr(emulator_module, "seed_device_if_needed", seed)

    result = await open_account_mobile("acc")

    assert (result.status, result.signed_in) == ("started", True)
    assert container.steps[-1] == "seeded", "the open returns once the seeding is done"


@pytest.mark.asyncio
async def test_a_failed_app_start_cancels_the_seeding_before_the_pod_is_undone(
    container: _Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _account()
    seeding = asyncio.Event()

    async def seed(_serial: str, _spec: object) -> bool:
        seeding.set()
        try:
            await asyncio.Event().wait()
        finally:
            container.step("seed_cancelled")
        return True

    async def ensure_app(_serial: str) -> None:
        await seeding.wait()
        code = "emulator_install_failed"
        raise EmulatorError(code)

    monkeypatch.setattr(emulator_module, "seed_device_if_needed", seed)
    monkeypatch.setattr(emulator_module, "ensure_app", ensure_app)

    with pytest.raises(EmulatorRefusedError):
        await open_account_mobile("acc")

    cancelled = container.steps.index("seed_cancelled")
    removed = next(i for i, step in enumerate(container.steps) if step.startswith("remove:"))
    assert cancelled < removed
