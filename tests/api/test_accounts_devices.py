"""Device endpoints: add the other device, pick the device at login/import and warming.

Thin routes: a refusal travels as the stable device code in the envelope's
``message`` (a flood wait keeps its seconds in ``fields``), so the SPA translates it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from services import warming as warming_service
from services.accounts import DeviceRefusedError
from tests.api.accounts_helpers import account as _account
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI

    from schemas.accounts import AccountRead, AccountSessionFileImport
    from schemas.warming import StartWarmingRequest, WarmingAccountState


@pytest.mark.asyncio
async def test_adding_a_device_answers_the_account(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    async def _fake(account_id: str, device_type: str) -> AccountRead:
        calls.append((account_id, device_type))
        return _account(account_id).model_copy(update={"devices": ["desktop", "mobile"]})

    monkeypatch.setattr("services.accounts.add_account_device", _fake)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/devices", json={"device_type": "mobile"})

    assert resp.status_code == 200
    assert resp.json()["devices"] == ["desktop", "mobile"]
    assert calls == [("acc-1", "mobile")]


@pytest.mark.asyncio
async def test_a_refused_device_answers_its_code_and_wait(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(account_id: str, device_type: str) -> AccountRead:  # noqa: ARG001
        code = "device_code_cooldown"
        raise DeviceRefusedError(code, retry_after_seconds=42)

    monkeypatch.setattr("services.accounts.add_account_device", _boom)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/devices", json={"device_type": "mobile"})

    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["message"] == "device_code_cooldown"
    assert error["fields"] == {"retry_after_seconds": "42"}


@pytest.mark.asyncio
async def test_adding_a_device_to_a_missing_account_is_404(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/ghost/devices", json={"device_type": "mobile"})

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_the_session_import_carries_the_declared_device(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    async def _fake(data: AccountSessionFileImport) -> AccountRead:
        seen.append(data.session_device)
        return _account("from-session")

    monkeypatch.setattr("services.accounts.import_account_session", _fake)
    async with _client(app) as client:
        default = await client.post(
            "/api/v1/accounts/import-session",
            files={"file": ("a.session", b"key", "application/octet-stream")},
        )
        mobile = await client.post(
            "/api/v1/accounts/import-session",
            files={"file": ("b.session", b"key", "application/octet-stream")},
            data={"session_device": "mobile"},
        )

    assert (default.status_code, mobile.status_code) == (200, 200)
    assert seen == ["desktop", "mobile"]


@pytest.mark.asyncio
async def test_a_phone_login_refused_for_mobile_answers_the_code(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(phone: str, label: str | None, session_device: str) -> AccountRead:  # noqa: ARG001
        assert session_device == "mobile"
        code = "mobile_api_not_configured"
        raise DeviceRefusedError(code)

    monkeypatch.setattr("services.accounts.start_phone_login", _boom)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/start-login",
            json={"phone": "+15550001111", "session_device": "mobile"},
        )

    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "mobile_api_not_configured"


@pytest.mark.asyncio
async def test_warming_on_a_missing_device_answers_the_code(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(body: StartWarmingRequest) -> WarmingAccountState:
        assert body.device_mode == "mobile"
        code = "device_missing"
        raise warming_service.WarmingDeviceUnavailableError(code, body.account_id)

    monkeypatch.setattr("services.warming.start_warming", _boom)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/warming/start",
            json={"account_id": "acc-1", "device_mode": "mobile"},
        )

    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "device_missing"
