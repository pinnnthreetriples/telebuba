"""API tests for telephony webhook validation, deduplication, status, manual input, and inbox."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from services.telephony import (
    clear_dispatcher_state,
    clear_sessions,
    start_waiting_session,
    wait_pending_dispatches,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

_ORIGIN = {"origin": "http://testserver"}


@pytest.fixture(autouse=True)
def _reset_telephony() -> None:
    clear_sessions()
    clear_dispatcher_state()


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.asyncio
async def test_sms_webhook_matches_active_session_and_filters_duplicates(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def instant_sleep(_sec: float) -> None:
        return

    async def fake_read_screen(_serial: str) -> tuple[str, list[object]]:
        return ("code", [])

    async def fake_human_type(_serial: str, _code: str) -> None:
        return

    monkeypatch.setattr("services.telephony._dispatcher.asyncio.sleep", instant_sleep)
    monkeypatch.setattr("services.telephony._dispatcher.telegram_app.read_screen", fake_read_screen)
    monkeypatch.setattr("services.telephony._dispatcher.adb.human_type", fake_human_type)

    start_waiting_session("telebuba-acc1", "+79991234567", platform="telegram")

    payload = {
        "phone": "+79991234567",
        "sender": "Telegram",
        "text": "Telegram code: 58392",
    }

    async with _client(app) as client:
        first = await client.post("/api/v1/telephony/sms-webhook", json=payload, headers=_ORIGIN)
        duplicate = await client.post(
            "/api/v1/telephony/sms-webhook", json=payload, headers=_ORIGIN
        )

    await wait_pending_dispatches()

    assert first.status_code == 200
    assert first.json() == {
        "status": "ok",
        "matched": True,
        "code": "58392",
        "duplicate": False,
    }

    assert duplicate.status_code == 200
    assert duplicate.json() == {
        "status": "ok",
        "matched": False,
        "code": None,
        "duplicate": True,
    }


@pytest.mark.asyncio
async def test_sms_webhook_validates_payload(app: FastAPI) -> None:
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/telephony/sms-webhook",
            json={"phone": "", "sender": ""},
            headers=_ORIGIN,
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_get_phone_status_returns_idle_and_active_session(app: FastAPI) -> None:
    async with _client(app) as client:
        idle_resp = await client.get("/api/v1/telephony/phones/telebuba-acc1/status")
        assert idle_resp.status_code == 200
        assert idle_resp.json()["status"] == "idle"

        start_waiting_session("telebuba-acc1", "+79991234567", platform="telegram", ttl_seconds=180)
        waiting_resp = await client.get("/api/v1/telephony/phones/telebuba-acc1/status")

    assert waiting_resp.status_code == 200
    body = waiting_resp.json()
    assert body["phone_id"] == "telebuba-acc1"
    assert body["phone_number"] == "+79991234567"
    assert body["status"] == "waiting_otp"
    assert body["remaining_seconds"] > 0


@pytest.mark.asyncio
async def test_manual_input_and_inbox_endpoints(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    typed: list[tuple[str, str]] = []

    async def fake_read_screen(_serial: str) -> tuple[str, list[object]]:
        return ("code", [])

    async def fake_human_type(serial: str, code: str) -> None:
        typed.append((serial, code))

    monkeypatch.setattr("services.telephony._dispatcher.telegram_app.read_screen", fake_read_screen)
    monkeypatch.setattr("services.telephony._dispatcher.adb.human_type", fake_human_type)

    start_waiting_session("telebuba-acc1", "+79991234567", platform="telegram")

    async with _client(app) as client:
        await client.post(
            "/api/v1/telephony/sms-webhook",
            json={
                "phone": "+79990009988",
                "sender": "TikTok",
                "text": "[TikTok] 774411 is your verification code",
            },
            headers=_ORIGIN,
        )
        inbox_resp = await client.get("/api/v1/telephony/inbox")
        manual_resp = await client.post(
            "/api/v1/telephony/phones/telebuba-acc1/manual-input",
            json={"code": "774411"},
            headers=_ORIGIN,
        )

    assert inbox_resp.status_code == 200
    messages = inbox_resp.json()["messages"]
    assert len(messages) == 1
    assert messages[0]["code"] == "774411"
    assert messages[0]["platform"] == "tiktok"

    assert manual_resp.status_code == 200
    assert manual_resp.json() == {
        "status": "ok",
        "phone_id": "telebuba-acc1",
        "code": "774411",
        "line_status": "completed",
    }
    assert typed == [("telebuba-acc1", "774411")]
