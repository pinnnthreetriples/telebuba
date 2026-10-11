"""API route tests for POST /api/v1/accounts/{account_id}/open-mobile."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from schemas.accounts import AccountCreate
from schemas.mobile_emulator import OpenMobileResult
from services.accounts.lifecycle import add_account
from services.accounts.mobile_emulator import NoProxyForMobileError
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.mark.asyncio
async def test_open_mobile_returns_result_for_existing_account(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await add_account(AccountCreate(account_id="acc-mob-api", phone="+79990001122"))

    async def fake_open_mobile(account_id: str) -> OpenMobileResult:
        return OpenMobileResult(
            launched=True,
            signed_in=True,
            instance_name=f"telebuba_{account_id}",
            message=None,
        )

    monkeypatch.setattr("services.accounts.open_account_mobile", fake_open_mobile)

    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-mob-api/open-mobile")

    assert resp.status_code == 200
    assert resp.json() == {
        "launched": True,
        "signed_in": True,
        "instance_name": "telebuba_acc-mob-api",
        "message": None,
    }


@pytest.mark.asyncio
async def test_open_mobile_returns_404_for_missing_account(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/missing-acc/open-mobile")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_open_mobile_returns_400_on_service_refusal(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await add_account(AccountCreate(account_id="acc-mob-noproxy", phone="+79990001133"))

    async def fail_no_proxy(_account_id: str) -> OpenMobileResult:
        msg = "mobile_emulator_no_proxy"
        raise NoProxyForMobileError(msg)

    monkeypatch.setattr("services.accounts.open_account_mobile", fail_no_proxy)

    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-mob-noproxy/open-mobile")

    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "mobile_emulator_no_proxy"
