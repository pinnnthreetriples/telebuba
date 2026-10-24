"""Passkey endpoint tests — thin routes over mocked passkey services."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from schemas.passkey import AccountPasskeyView
from services.accounts import AccountActionError, AccountNotFoundError
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI

_URL = "/api/v1/accounts/acc-1/passkey"


def _view(*, has_passkey: bool) -> AccountPasskeyView:
    return AccountPasskeyView(
        account_id="acc-1",
        has_passkey=has_passkey,
        created_at="2026-10-24T12:00:00+00:00" if has_passkey else None,
        vault_configured=True,
    )


@pytest.mark.parametrize(
    ("method", "url", "service", "has_passkey"),
    [
        ("get", _URL, "read_account_passkey", True),
        ("post", _URL, "create_account_passkey", True),
        ("post", f"{_URL}/login", "relogin_with_passkey", True),
        ("delete", _URL, "remove_account_passkey", False),
    ],
)
@pytest.mark.asyncio
async def test_each_route_answers_the_service_view(  # noqa: PLR0913 - one row of the table
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    url: str,
    service: str,
    has_passkey: bool,  # noqa: FBT001 - parametrized
) -> None:
    async def _fake(account_id: str) -> AccountPasskeyView:
        assert account_id == "acc-1"
        return _view(has_passkey=has_passkey)

    monkeypatch.setattr(f"services.accounts.{service}", _fake)
    async with _client(app) as client:
        resp = await getattr(client, method)(url)

    assert resp.status_code == 200
    assert resp.json() == _view(has_passkey=has_passkey).model_dump()


@pytest.mark.asyncio
async def test_an_unknown_account_is_404(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _missing(account_id: str) -> AccountPasskeyView:
        raise AccountNotFoundError(account_id)

    monkeypatch.setattr("services.accounts.read_account_passkey", _missing)
    async with _client(app) as client:
        resp = await client.get(_URL)

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_a_refusal_answers_its_stable_code(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _refused(_account_id: str) -> AccountPasskeyView:
        code = "passkey_vault_disabled"
        raise AccountActionError(code)

    monkeypatch.setattr("services.accounts.create_account_passkey", _refused)
    async with _client(app) as client:
        resp = await client.post(_URL)

    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "passkey_vault_disabled"
