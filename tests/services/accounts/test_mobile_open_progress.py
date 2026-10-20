"""What the operator's phone dialog sees of an open: its stages, its end, its refusals.

The container layer is the scripted fake (``_mobile_support``); the progress is read
through the service, the way the API serves it to the SPA's poll.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from services.accounts import _mobile_start, open_mobile_progress, remove_account
from services.accounts import mobile_emulator as emulator_module
from services.accounts.mobile_emulator import EmulatorRefusedError, open_account_mobile
from tests.services.accounts._mobile_support import _account, _Container

if TYPE_CHECKING:
    from schemas.emulator import MobileOpenStage

pytestmark = pytest.mark.usefixtures("mobile_enabled")


def _stage() -> tuple[MobileOpenStage | None, str | None]:
    progress = open_mobile_progress("acc")
    return progress.stage, progress.reason


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, object]]]:
    logged: list[tuple[str, dict[str, object]]] = []

    async def fake_log(
        _level: str,
        event: str,
        *,
        account_id: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        assert account_id == "acc"
        logged.append((event, extra or {}))

    monkeypatch.setattr(_mobile_start, "log_event", fake_log)
    monkeypatch.setattr(emulator_module, "log_event", fake_log)
    return logged


@pytest.mark.asyncio
async def test_an_account_never_opened_has_no_stage_but_names_its_phone(
    container: _Container,
) -> None:
    await _account()

    progress = open_mobile_progress("acc")

    assert (progress.phone_id, progress.stage) == ("telebuba-acc", None)
    assert container.steps == []


@pytest.mark.asyncio
async def test_each_stage_shows_while_the_open_is_in_it(container: _Container) -> None:
    await _account()
    container.launch_gate, container.boot_gate = asyncio.Event(), asyncio.Event()
    container.screens = ["chats"]
    start = asyncio.create_task(open_account_mobile("acc"))

    await container.launched.wait()
    assert _stage() == ("pod", None)
    container.launch_gate.set()
    await container.booting.wait()
    assert _stage() == ("boot", None)
    container.boot_gate.set()
    await start

    assert _stage() == ("done", None)


@pytest.mark.asyncio
async def test_a_sign_in_that_stops_short_is_logged_and_ends_by_hand(
    container: _Container,
    events: list[tuple[str, dict[str, object]]],
) -> None:
    await _account()
    container.code = "pool-down"
    container.screens = ["phone", "code"]

    await open_account_mobile("acc")

    assert _stage() == ("manual", "device_primary_unavailable")
    assert ("account_mobile_sign_in_failed", {"reason": "device_primary_unavailable"}) in events
    assert ("account_mobile_opened", {}) in events


@pytest.mark.asyncio
async def test_a_signed_out_app_that_shows_no_entry_screen_ends_by_hand(
    container: _Container,
) -> None:
    await _account()
    container.screens = ["unknown"]

    result = await open_account_mobile("acc")

    assert (result.signed_in, result.sign_in_refusal) == (False, None)
    assert _stage() == ("manual", None)


@pytest.mark.asyncio
async def test_a_failed_open_ends_failed_with_its_code(
    container: _Container,
    events: list[tuple[str, dict[str, object]]],
) -> None:
    await _account()
    container.fail_at = "boot"

    with pytest.raises(EmulatorRefusedError):
        await open_account_mobile("acc")

    assert _stage() == ("failed", "emulator_boot_timeout")
    assert ("account_mobile_open_failed", {"reason": "emulator_boot_timeout"}) in events


@pytest.mark.asyncio
async def test_a_refusal_before_any_container_is_logged_too(
    container: _Container,
    events: list[tuple[str, dict[str, object]]],
) -> None:
    await _account(proxy_type=None)

    with pytest.raises(EmulatorRefusedError):
        await open_account_mobile("acc")

    assert _stage() == ("failed", "emulator_proxy_missing")
    assert events == [("account_mobile_open_failed", {"reason": "emulator_proxy_missing"})]
    assert container.steps == []


@pytest.mark.asyncio
async def test_an_open_broken_outside_its_refusals_still_ends_failed(
    container: _Container,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account()

    async def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError

    monkeypatch.setattr(emulator_module, "start_and_sign_in", broken)

    with pytest.raises(RuntimeError):
        await open_account_mobile("acc")

    assert _stage() == ("failed", None)
    assert container.steps == ["find:telebuba-acc"]


@pytest.mark.asyncio
async def test_a_running_signed_out_app_hands_its_refusal_back_too(
    container: _Container,
) -> None:
    await _account()
    await open_account_mobile("acc")  # a pod now runs, on the current proxy
    container.screens = ["phone", "phone", "code"]
    container.code = None

    result = await open_account_mobile("acc")

    assert result.status == "already_running"
    assert (result.signed_in, result.sign_in_refusal) == (False, "emulator_code_timeout")
    assert _stage() == ("manual", "emulator_code_timeout")


@pytest.mark.asyncio
async def test_removing_the_account_forgets_its_progress(container: _Container) -> None:
    await _account()
    await open_account_mobile("acc")

    await remove_account("acc")

    assert _stage() == (None, None)
    assert "remove:telebuba-acc" in container.steps
