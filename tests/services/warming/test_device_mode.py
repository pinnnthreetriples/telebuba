"""Tests for Stage 4: Warming Engine Mobile Device Mode and fallback."""

from __future__ import annotations

import pytest

from core.db import (
    create_account,
    fetch_warming_state,
    load_warming_settings,
    upsert_device_fingerprint,
    upsert_warming_state,
)
from schemas.accounts import AccountCreate
from schemas.device_fingerprint import DeviceFingerprint, DeviceType
from schemas.telegram_actions import ActionResult, TelegramAction
from schemas.warming import (
    StartWarmingRequest,
    WarmingCycleRequest,
    WarmingSettingsUpdate,
    WarmingStateWrite,
)
from services import warming
from services.warming import _cycle, _seams
from services.warming._start_state import carry_or_restamp
from services.warming._state import _set_state
from tests.services.warming._support import _seed_channel, _seed_ready_account


@pytest.mark.asyncio
async def test_warming_settings_device_mode_default_and_roundtrip() -> None:
    """warming_settings defaults device_mode to 'mobile' and persists updates."""
    initial = await warming.load_settings()
    assert initial.device_mode == "mobile"

    updated = await warming.save_settings(WarmingSettingsUpdate(device_mode="desktop"))
    assert updated.device_mode == "desktop"

    secret = await load_warming_settings()
    assert secret.device_mode == "desktop"

    # Omitting device_mode in a subsequent update keeps the stored value
    kept = await warming.save_settings(WarmingSettingsUpdate(reactions_enabled=False))
    assert kept.device_mode == "desktop"


@pytest.mark.asyncio
async def test_warming_state_record_persists_device_mode() -> None:
    """warming_account_state persists device_mode and carries it across transitions."""
    await create_account(AccountCreate(account_id="acc-mode"))

    written = await _set_state(
        "acc-mode",
        "active",
        device_mode="desktop",
    )
    assert written.record.device_mode == "desktop"

    # Unspecified transition carries device_mode
    transitioned = await _set_state("acc-mode", "sleeping", last_event="parked")
    assert transitioned.record.device_mode == "desktop"

    loaded = await fetch_warming_state("acc-mode")
    assert loaded is not None
    assert loaded.device_mode == "desktop"


@pytest.mark.asyncio
async def test_carry_or_restamp_preserves_in_flight_device_mode() -> None:
    """carry_or_restamp keeps existing device_mode when continuing or uses request on fresh."""
    await create_account(AccountCreate(account_id="acc-stint"))
    fresh = carry_or_restamp(
        None,
        StartWarmingRequest(account_id="acc-stint", device_mode="desktop"),
    )
    assert fresh.device_mode == "desktop"

    active_res = await upsert_warming_state(
        WarmingStateWrite(account_id="acc-stint", state="active", device_mode="desktop"),
    )
    continued = carry_or_restamp(
        active_res.record,
        StartWarmingRequest(account_id="acc-stint", device_mode="mobile"),
    )
    assert continued.device_mode == "desktop"


@pytest.mark.asyncio
async def test_start_warming_stamps_device_mode_onto_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """start_warming writes the requested device_mode to state and returns it on the card."""
    await _seed_ready_account("acc-start-mode")
    monkeypatch.setattr(warming._runtime, "_spawn_runtime_task", lambda *_args: None)

    card = await warming.start_warming(
        StartWarmingRequest(account_id="acc-start-mode", device_mode="desktop"),
    )
    assert card.device_mode == "desktop"
    state = await fetch_warming_state("acc-start-mode")
    assert state is not None
    assert state.device_mode == "desktop"


@pytest.mark.asyncio
async def test_run_one_cycle_dispatches_mobile_when_account_has_mobile_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When device_mode='mobile' and mobile profile exists, gateway gets device_type='mobile'."""
    await create_account(AccountCreate(account_id="acc-mob"))
    await upsert_device_fingerprint(
        DeviceFingerprint(
            account_id="acc-mob",
            device_type="mobile",
            platform="android",
            device_model="Samsung SM-S928B",
            system_version="SDK 34",
            app_version="12.5.1 (64120)",
            lang_code="en",
            system_lang_code="en-US",
        ),
    )
    await _seed_channel()
    monkeypatch.setattr(_seams, "sleep", lambda _s: _noop())

    dispatched_modes: list[DeviceType] = []

    async def fake_gateway_execute(
        account_id: str,
        action: TelegramAction,
        *,
        domain: str = "default",
        device_type: DeviceType = "desktop",
    ) -> ActionResult:
        assert domain == "warming"
        dispatched_modes.append(device_type)
        return ActionResult(
            status="ok",
            action_type=action.action_type,
            account_id=account_id,
            recent_message_ids=["101"] if action.action_type == "read_channel" else None,
        )

    monkeypatch.setattr(_seams, "_gateway_execute", fake_gateway_execute)

    result = await _cycle.run_one_cycle(
        WarmingCycleRequest(
            account_id="acc-mob",
            remaining_actions=2,
            dm_allowed=False,
            device_mode="mobile",
        ),
    )
    assert result.status == "ok"
    assert dispatched_modes
    assert all(mode == "mobile" for mode in dispatched_modes)


@pytest.mark.asyncio
async def test_run_one_cycle_falls_back_to_desktop_when_mobile_profile_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When device_mode='mobile' but mobile profile is missing, cycle falls back to 'desktop'."""
    await create_account(AccountCreate(account_id="acc-no-mob"))
    await _seed_channel()
    monkeypatch.setattr(_seams, "sleep", lambda _s: _noop())

    dispatched_modes: list[DeviceType] = []

    async def fake_gateway_execute(
        account_id: str,
        action: TelegramAction,
        *,
        domain: str = "default",
        device_type: DeviceType = "desktop",
    ) -> ActionResult:
        assert domain == "warming"
        dispatched_modes.append(device_type)
        return ActionResult(
            status="ok",
            action_type=action.action_type,
            account_id=account_id,
            recent_message_ids=["101"] if action.action_type == "read_channel" else None,
        )

    monkeypatch.setattr(_seams, "_gateway_execute", fake_gateway_execute)

    result = await _cycle.run_one_cycle(
        WarmingCycleRequest(
            account_id="acc-no-mob",
            remaining_actions=2,
            dm_allowed=False,
            device_mode="mobile",
        ),
    )
    assert result.status == "ok"
    assert dispatched_modes
    assert all(mode == "desktop" for mode in dispatched_modes)


async def _noop() -> None:
    return None
