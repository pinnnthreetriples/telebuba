"""``/api/v1/notifications``: masked settings round-trip and the test send's error mapping."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from schemas.notifications import NotificationTestResult
from services import notification_settings

if TYPE_CHECKING:
    from fastapi import FastAPI

_TOKEN = "123456:AAH-secret_token_value_abcdefXYZ"


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_settings_round_trip_exposes_only_a_mask(app: FastAPI) -> None:
    async with _client(app) as client:
        empty = await client.get("/api/v1/notifications/settings")
        put = await client.put(
            "/api/v1/notifications/settings",
            json={"enabled": True, "bot_token": _TOKEN, "chat_id": "-100500"},
        )
        got = await client.get("/api/v1/notifications/settings")
    assert empty.status_code == 200
    assert empty.json()["has_bot_token"] is False
    for response in (put, got):
        assert response.status_code == 200
        body = response.json()
        assert body["has_bot_token"] is True
        assert body["chat_id"] == "-100500"
        assert _TOKEN not in response.text
        assert "bot_token" not in body


@pytest.mark.asyncio
async def test_a_malformed_token_is_a_422(app: FastAPI) -> None:
    async with _client(app) as client:
        response = await client.put(
            "/api/v1/notifications/settings",
            json={"bot_token": "not-a-token/../x"},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def _raising(exc: Exception):
    async def _send() -> NotificationTestResult:
        raise exc

    return _send


@pytest.mark.parametrize(
    ("exc", "status_code", "message"),
    [
        (
            notification_settings.NotificationsNotConfiguredError(),
            409,
            "notifications_not_configured",
        ),
        (notification_settings.NotificationDeliveryError("rejected"), 400, "telegram_rejected"),
        (
            notification_settings.NotificationDeliveryError("unreachable"),
            503,
            "telegram_unreachable",
        ),
    ],
)
@pytest.mark.asyncio
async def test_test_send_failures_map_to_stable_codes(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    status_code: int,
    message: str,
) -> None:
    monkeypatch.setattr(notification_settings, "send_test_notification", _raising(exc))
    async with _client(app) as client:
        response = await client.post("/api/v1/notifications/test")
    assert response.status_code == status_code
    assert response.json()["error"]["message"] == message


@pytest.mark.asyncio
async def test_a_delivered_test_answers_sent(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _send() -> NotificationTestResult:
        return NotificationTestResult()

    monkeypatch.setattr(notification_settings, "send_test_notification", _send)
    async with _client(app) as client:
        response = await client.post("/api/v1/notifications/test")
    assert response.status_code == 200
    assert response.json() == {"sent": True}
