"""A container's first-boot seeding inside the open: it never decides whether the open works."""

from __future__ import annotations

import pytest

from services.accounts import mobile_emulator as emulator_module
from services.accounts.mobile_emulator import open_account_mobile
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
