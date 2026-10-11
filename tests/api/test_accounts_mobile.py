"""API endpoint tests for mobile Android emulator launcher."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.emulator.config import EmulatorNotFoundError, NoProxyForMobileError
from schemas.accounts import AccountCreate
from schemas.mobile_emulator import OpenMobileResult
from services.accounts import add_account
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.mark.asyncio
async def test_open_mobile_success(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str) -> OpenMobileResult:  # noqa: ARG001
        return OpenMobileResult(
            launched=True,
            signed_in=True,
            instance_name="telebuba_acc-1",
        )

    monkeypatch.setattr("services.accounts.open_account_mobile", _fake)
    await add_account(AccountCreate(account_id="acc-1"))
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/open-mobile")
    assert resp.status_code == 200
    assert resp.json() == {
        "launched": True,
        "signed_in": True,
        "instance_name": "telebuba_acc-1",
        "message": None,
    }


@pytest.mark.asyncio
async def test_open_mobile_reports_not_signed_in(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str) -> OpenMobileResult:  # noqa: ARG001
        return OpenMobileResult(
            launched=True,
            signed_in=False,
            instance_name="telebuba_acc-1",
            message="Login code timed out",
        )

    monkeypatch.setattr("services.accounts.open_account_mobile", _fake)
    await add_account(AccountCreate(account_id="acc-1"))
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/open-mobile")
    assert resp.status_code == 200
    assert resp.json() == {
        "launched": True,
        "signed_in": False,
        "instance_name": "telebuba_acc-1",
        "message": "Login code timed out",
    }


@pytest.mark.asyncio
async def test_open_mobile_missing_account_is_404(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str) -> OpenMobileResult:  # noqa: ARG001
        msg = "service must not be reached for a missing account"
        raise AssertionError(msg)

    monkeypatch.setattr("services.accounts.open_account_mobile", _fake)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-missing/open-mobile")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_open_mobile_no_proxy_is_400(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str) -> OpenMobileResult:  # noqa: ARG001
        msg = "account has no proxy assigned"
        raise NoProxyForMobileError(msg)

    monkeypatch.setattr("services.accounts.open_account_mobile", _fake)
    await add_account(AccountCreate(account_id="acc-1"))
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/open-mobile")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_open_mobile_no_emulator_is_400(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str) -> OpenMobileResult:  # noqa: ARG001
        msg = "LDPlayer or ADB binary not located on host"
        raise EmulatorNotFoundError(msg)

    monkeypatch.setattr("services.accounts.open_account_mobile", _fake)
    await add_account(AccountCreate(account_id="acc-1"))
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/open-mobile")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"
