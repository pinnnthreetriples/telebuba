"""API tests: the gateway webhook's own auth, the phone's wait/status, manual input, inbox."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest

from api import create_app
from core.config import settings
from core.emulator._errors import EmulatorError
from services.telephony import (
    _dispatcher,
    clear_dispatcher_state,
    clear_sessions,
    start_waiting_session,
    wait_pending_dispatches,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI

    from core.emulator.ui_dump import Screen, UiNode

_ORIGIN = {"origin": "http://testserver"}
# A throwaway value for this test process only, never a real gateway secret.
_TOKEN = "test-only-telephony-webhook-token-0123456789"
_WEBHOOK = "/api/v1/telephony/sms-webhook"
_SMS = {"phone": "+79991234567", "sender": "Telegram", "text": "Telegram code: 58392"}


@pytest.fixture(autouse=True)
def _reset_telephony(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings.telephony, "webhook_secret", _TOKEN)
    clear_sessions()
    clear_dispatcher_state()
    yield
    clear_sessions()
    clear_dispatcher_state()


@pytest.fixture
def typed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    async def resolve_serial(phone_id: str) -> str:
        return phone_id

    async def read_screen(_serial: str) -> tuple[Screen, list[UiNode]]:
        return "code", []

    async def enter_code(serial: str, _nodes: list[UiNode], code: str) -> None:
        calls.append((serial, code))

    monkeypatch.setattr(_dispatcher, "_resolve_serial", resolve_serial)
    monkeypatch.setattr(_dispatcher.telegram_app, "read_screen", read_screen)
    monkeypatch.setattr(_dispatcher.telegram_app, "enter_code", enter_code)
    monkeypatch.setattr(_dispatcher._RNG, "uniform", lambda _low, _high: 0.0)
    return calls


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


def _gateway_headers(token: str = _TOKEN) -> dict[str, str]:
    return {**_ORIGIN, "x-telephony-token": token}


@pytest.mark.asyncio
async def test_a_gateway_needs_no_operator_session_only_the_token(
    typed: list[tuple[str, str]],
) -> None:
    raw_app = create_app()  # the real session gate, no override
    start_waiting_session("telebuba-acc1", "+79991234567")

    async with _client(raw_app) as client:
        first = await client.post(_WEBHOOK, json=_SMS, headers=_gateway_headers())
        duplicate = await client.post(_WEBHOOK, json=_SMS, headers=_gateway_headers())
    await wait_pending_dispatches()

    assert first.status_code == 200
    assert first.json() == {"status": "ok", "matched": True, "code": "58392", "duplicate": False}
    assert duplicate.json() == {"status": "ok", "matched": False, "code": None, "duplicate": True}
    assert typed == [("telebuba-acc1", "58392")]


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [_ORIGIN, _gateway_headers("wrong-token")])
async def test_a_gateway_without_the_token_is_refused(
    app: FastAPI,
    headers: dict[str, str],
) -> None:
    async with _client(app) as client:
        response = await client.post(_WEBHOOK, json=_SMS, headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "telephony_webhook_unauthorized"


@pytest.mark.asyncio
async def test_the_webhook_is_off_until_a_secret_is_configured(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.telephony, "webhook_secret", "")

    async with _client(app) as client:
        response = await client.post(_WEBHOOK, json=_SMS, headers=_gateway_headers())

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "telephony_webhook_disabled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"phone": "", "sender": ""},
        {**_SMS, "timestamp": "2026-10-19T12:00:00"},  # no timezone
        {**_SMS, "kind": "flash_call"},  # no caller_id
        {**_SMS, "text": "x" * 2001},
        {**_SMS, "phone": "+" + "1" * 40},
    ],
)
async def test_malformed_payloads_are_refused(app: FastAPI, payload: dict[str, str]) -> None:
    async with _client(app) as client:
        response = await client.post(_WEBHOOK, json=payload, headers=_gateway_headers())

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_a_replayed_webhook_with_an_old_timestamp_is_refused(app: FastAPI) -> None:
    stale = {**_SMS, "timestamp": "2020-01-01T00:00:00Z"}

    async with _client(app) as client:
        response = await client.post(_WEBHOOK, json=stale, headers=_gateway_headers())

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "webhook_timestamp_out_of_window"


@pytest.mark.asyncio
async def test_the_phone_status_goes_from_idle_to_waiting(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def require_account(account_id: str) -> SimpleNamespace:
        return SimpleNamespace(account_id=account_id, phone="+79991234567")

    monkeypatch.setattr(_dispatcher, "require_account", require_account)
    monkeypatch.setattr(_dispatcher, "phone_activity", lambda _account_id: None)

    async with _client(app) as client:
        idle = await client.get("/api/v1/telephony/phones/telebuba-acc1/status")
        waiting = await client.post("/api/v1/telephony/phones/telebuba-acc1/wait", headers=_ORIGIN)
        status = await client.get("/api/v1/telephony/phones/telebuba-acc1/status")

    assert idle.json()["status"] == "idle"
    assert waiting.status_code == 200
    body = status.json()
    assert (body["status"], body["phone_number"]) == ("waiting_otp", "+79991234567")
    assert body["remaining_seconds"] > 0


@pytest.mark.asyncio
async def test_waiting_on_something_that_is_not_a_phone_is_refused(app: FastAPI) -> None:
    async with _client(app) as client:
        response = await client.post("/api/v1/telephony/phones/not-a-phone/wait", headers=_ORIGIN)

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "emulator_not_found"


@pytest.mark.asyncio
async def test_manual_input_types_and_the_inbox_lists(
    app: FastAPI,
    typed: list[tuple[str, str]],
) -> None:
    tiktok = {"phone": "+79990009988", "sender": "TikTok", "text": "[TikTok] 774411 is your code"}

    async with _client(app) as client:
        await client.post(_WEBHOOK, json=tiktok, headers=_gateway_headers())
        inbox = await client.get("/api/v1/telephony/inbox")
        manual = await client.post(
            "/api/v1/telephony/phones/telebuba-acc1/manual-input",
            json={"code": "774411"},
            headers=_ORIGIN,
        )

    (message,) = inbox.json()["messages"]
    assert (message["code"], message["platform"]) == ("774411", "tiktok")
    assert manual.json() == {
        "status": "ok",
        "phone_id": "telebuba-acc1",
        "code": "774411",
        "line_status": "completed",
    }
    assert typed == [("telebuba-acc1", "774411")]


@pytest.mark.asyncio
async def test_a_refused_manual_input_answers_its_code(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolve_serial(_phone_id: str) -> str:
        code = "emulator_not_running"
        raise EmulatorError(code)

    monkeypatch.setattr(_dispatcher, "_resolve_serial", resolve_serial)

    async with _client(app) as client:
        response = await client.post(
            "/api/v1/telephony/phones/telebuba-acc1/manual-input",
            json={"code": "123456"},
            headers=_ORIGIN,
        )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "emulator_not_running"


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["123", "123456789", "12a45", "١٢٣٤"])
async def test_manual_input_takes_four_to_eight_ascii_digits(app: FastAPI, code: str) -> None:
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/telephony/phones/telebuba-acc1/manual-input",
            json={"code": code},
            headers=_ORIGIN,
        )

    assert response.status_code == 422
