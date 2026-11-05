"""The phone-login routes answer a Telegram refusal as a stable code, never prose."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from services.accounts import AccountActionError
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI

    from schemas.accounts import AccountRead
    from schemas.phone_login import PhoneCodeRequestResult


@pytest.mark.asyncio
async def test_submit_code_flood_wait_travels_as_code_and_seconds(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(account_id: str, code: str, password: str | None) -> AccountRead:  # noqa: ARG001
        code = "flood_wait"
        raise AccountActionError(code, retry_after_seconds=42)

    monkeypatch.setattr("services.accounts.submit_login_code", _boom)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/submit-code", json={"code": "00000"})
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert (error["message"], error["fields"]) == ("flood_wait", {"retry_after_seconds": "42"})


@pytest.mark.asyncio
async def test_request_code_timeout_is_a_503_unavailable(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(account_id: str) -> PhoneCodeRequestResult:  # noqa: ARG001
        code = "unavailable"
        raise AccountActionError(code)

    monkeypatch.setattr("services.accounts.request_login_code", _boom)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/request-code")
    assert resp.status_code == 503
    assert resp.json()["error"]["message"] == "unavailable"
