"""Tests for the SMS dispatcher, session store, cognitive pause, and ADB biomechanical typing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.emulator.ui_dump import UiNode
from schemas.telephony import InboundSmsWebhook
from services.telephony import (
    COGNITIVE_PAUSE_MAX_SEC,
    COGNITIVE_PAUSE_MIN_SEC,
    INBOX_MAX_MESSAGES,
    clear_dispatcher_state,
    clear_sessions,
    find_active_session,
    get_phone_status,
    handle_inbound_sms,
    list_inbox,
    manual_input_otp,
    normalize_phone,
    start_waiting_session,
    wait_pending_dispatches,
)

if TYPE_CHECKING:
    from core.emulator.ui_dump import Screen


@pytest.fixture(autouse=True)
def _clean_telephony_state() -> None:
    clear_sessions()
    clear_dispatcher_state()


@pytest.mark.parametrize(
    ("raw_phone", "expected"),
    [
        ("+7 (999) 123-45-67", "+79991234567"),
        ("8-999-123-45-67", "+79991234567"),
        ("89991234567", "+79991234567"),
        ("79991234567", "+79991234567"),
        ("+1 555-000-1111", "+15550001111"),
        ("   ", ""),
    ],
)
def test_normalize_phone_handles_russian_and_e164_variants(
    raw_phone: str,
    expected: str,
) -> None:
    assert normalize_phone(raw_phone) == expected


@pytest.mark.asyncio
async def test_dispatcher_waits_at_least_five_seconds_and_calls_adb_human_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []
    typed_calls: list[tuple[str, str]] = []
    tapped_nodes: list[tuple[str, UiNode]] = []
    observed_statuses: list[str] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        status = await get_phone_status("telebuba-acc1")
        observed_statuses.append(status.status)

    async def fake_read_screen(_serial: str) -> tuple[Screen, list[UiNode]]:
        return (
            "unknown",
            [
                UiNode(
                    text="",
                    description="Code input",
                    class_name="android.widget.EditText",
                    password=False,
                    bounds=(200, 400, 600, 480),
                )
            ],
        )

    async def fake_human_tap(serial: str, node: UiNode) -> None:
        tapped_nodes.append((serial, node))

    async def fake_human_type(serial: str, text: str) -> None:
        status = await get_phone_status("telebuba-acc1")
        observed_statuses.append(status.status)
        typed_calls.append((serial, text))

    monkeypatch.setattr("services.telephony._dispatcher.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("services.telephony._dispatcher.telegram_app.read_screen", fake_read_screen)
    monkeypatch.setattr("services.telephony._dispatcher.adb.human_tap", fake_human_tap)
    monkeypatch.setattr("services.telephony._dispatcher.adb.human_type", fake_human_type)

    start_waiting_session("telebuba-acc1", "8 (999) 123-45-67", platform="telegram")

    webhook = InboundSmsWebhook(
        phone="+79991234567",
        sender="Telegram",
        text="Telegram code: 48291",
    )
    response = await handle_inbound_sms(webhook)
    assert response.status == "ok"
    assert response.matched is True
    assert response.code == "48291"

    await wait_pending_dispatches()

    assert len(slept) == 1
    assert COGNITIVE_PAUSE_MIN_SEC <= slept[0] <= COGNITIVE_PAUSE_MAX_SEC
    assert slept[0] >= 5.0
    assert observed_statuses == ["otp_received", "typing"]
    assert len(tapped_nodes) == 1
    assert tapped_nodes[0][0] == "telebuba-acc1"
    assert typed_calls == [("telebuba-acc1", "48291")]

    final_status = await get_phone_status("telebuba-acc1")
    assert final_status.status == "completed"
    assert final_status.last_code == "48291"


@pytest.mark.asyncio
async def test_unmatched_sms_falls_into_fallback_inbox_without_typing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    typed_calls: list[tuple[str, str]] = []

    async def fake_human_type(serial: str, text: str) -> None:
        typed_calls.append((serial, text))

    monkeypatch.setattr("services.telephony._dispatcher.adb.human_type", fake_human_type)

    webhook = InboundSmsWebhook(
        phone="+79990001122",
        sender="WhatsApp",
        text="<#> Ваш код WhatsApp: 321-654",
    )
    response = await handle_inbound_sms(webhook)

    assert response.status == "ok"
    assert response.matched is False
    assert response.code == "321654"
    assert typed_calls == []

    inbox = await list_inbox()
    assert len(inbox.messages) == 1
    assert inbox.messages[0].phone == "+79990001122"
    assert inbox.messages[0].code == "321654"
    assert inbox.messages[0].matched is False
    assert inbox.messages[0].platform == "whatsapp"


@pytest.mark.asyncio
async def test_inbox_ring_buffer_caps_at_fifty_messages() -> None:
    for i in range(65):
        await handle_inbound_sms(
            InboundSmsWebhook(
                phone=f"+7999000{i:04d}",
                sender="Telegram",
                text=f"Telegram code: {10000 + i}",
            )
        )

    inbox = await list_inbox()
    assert len(inbox.messages) == INBOX_MAX_MESSAGES
    assert inbox.messages[0].code == "10064"
    assert inbox.messages[-1].code == "10015"


@pytest.mark.asyncio
async def test_expired_session_transitions_to_timeout() -> None:
    session = start_waiting_session("telebuba-acc2", "+79995554433", ttl_seconds=60)
    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    assert find_active_session("+79995554433") is None
    status = await get_phone_status("telebuba-acc2")
    assert status.status == "timeout"
    assert status.remaining_seconds == 0


@pytest.mark.asyncio
async def test_manual_input_types_code_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    typed: list[tuple[str, str]] = []

    async def fake_read_screen(_serial: str) -> tuple[Screen, list[UiNode]]:
        return ("code", [])

    async def fake_human_type(serial: str, text: str) -> None:
        typed.append((serial, text))

    monkeypatch.setattr("services.telephony._dispatcher.telegram_app.read_screen", fake_read_screen)
    monkeypatch.setattr("services.telephony._dispatcher.adb.human_type", fake_human_type)

    start_waiting_session("telebuba-acc3", "+79997778899", platform="telegram")
    result = await manual_input_otp("telebuba-acc3", "654321")

    assert result.status == "ok"
    assert result.phone_id == "telebuba-acc3"
    assert result.code == "654321"
    assert result.line_status == "completed"
    assert typed == [("telebuba-acc3", "654321")]
