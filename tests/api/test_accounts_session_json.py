"""Session-import with the seller's JSON, and recording a password an account already has."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from schemas.twofa import AccountTwoFactorView
from services.accounts import AccountActionError
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI

FAKE_PASSWORD = "fake-pass-1234567"
PHONE = "79990001122"
_IMPORT_URL = "/api/v1/accounts/import-session"


def _json_bytes(**overrides: object) -> bytes:
    raw: dict[str, object] = {
        "twoFA": FAKE_PASSWORD,
        "device": "Samsung Galaxy S23",
        "sdk": "Android 14",
        "app_version": "10.9.1",
        "phone": PHONE,
    }
    raw.update(overrides)
    return json.dumps(raw).encode()


@pytest.fixture(autouse=True)
def _mobile_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.telegram, "mobile_api_id", 6)
    monkeypatch.setattr(settings.telegram, "mobile_api_hash", "mobile-hash")


@pytest.mark.asyncio
async def test_import_with_json_answers_with_the_recorded_flag(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _record(account_id: str, password: str) -> bool:
        assert (account_id, password) == (PHONE, FAKE_PASSWORD)
        return True

    monkeypatch.setattr("services.accounts.sessions.record_account_twofa", _record)
    async with _client(app) as client:
        resp = await client.post(
            _IMPORT_URL,
            files={
                "file": (f"{PHONE}.session", b"session-bytes", "application/octet-stream"),
                "json_file": (f"{PHONE}.json", _json_bytes(), "application/json"),
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["account_id"] == PHONE
    assert body["twofa_recorded"] is True
    assert body["session_device"] == "mobile"
    assert FAKE_PASSWORD not in resp.text


@pytest.mark.asyncio
async def test_import_without_json_has_no_recorded_flag(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post(
            _IMPORT_URL,
            files={"file": (f"{PHONE}.session", b"session-bytes", "application/octet-stream")},
        )

    assert resp.status_code == 200
    assert resp.json()["twofa_recorded"] is None


@pytest.mark.asyncio
async def test_a_json_phone_that_differs_from_the_file_is_a_coded_400(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post(
            _IMPORT_URL,
            files={
                "file": (f"{PHONE}.session", b"session-bytes", "application/octet-stream"),
                "json_file": ("x.json", _json_bytes(phone="79990009999"), "application/json"),
            },
        )

    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "session_json_phone_mismatch"


@pytest.mark.asyncio
async def test_a_broken_json_is_a_coded_400(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post(
            _IMPORT_URL,
            files={
                "file": (f"{PHONE}.session", b"session-bytes", "application/octet-stream"),
                "json_file": ("x.json", b"not json", "application/json"),
            },
        )

    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "session_json_invalid"


@pytest.mark.asyncio
async def test_record_route_verifies_then_answers_with_the_boolean_view(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _record(account_id: str, password: str) -> bool:
        assert (account_id, password) == ("acc-1", FAKE_PASSWORD)
        return True

    async def _read(account_id: str) -> AccountTwoFactorView:  # noqa: ARG001
        return AccountTwoFactorView(has_stored_password=True)

    monkeypatch.setattr("services.accounts.record_account_twofa", _record)
    monkeypatch.setattr("services.accounts.read_account_twofa", _read)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/2fa/record", json={"password": FAKE_PASSWORD}
        )

    assert resp.status_code == 200
    assert resp.json()["has_stored_password"] is True
    assert FAKE_PASSWORD not in resp.text


@pytest.mark.asyncio
async def test_record_route_refusal_is_the_stable_code(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _refuse(account_id: str, password: str) -> bool:  # noqa: ARG001
        code = "twofa_current_password_invalid"
        raise AccountActionError(code)

    monkeypatch.setattr("services.accounts.record_account_twofa", _refuse)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/2fa/record", json={"password": "wrong"})

    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "twofa_current_password_invalid"


@pytest.mark.asyncio
async def test_record_route_unknown_account_is_404(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/nope/2fa/record", json={"password": "x"})

    assert resp.status_code == 404
