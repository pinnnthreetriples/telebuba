"""Passkey endpoint tests — thin routes over mocked passkey services."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from schemas.passkey import AccountPasskeyCheck, AccountPasskeyView
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
    async def _fake(account_id: str, **_: object) -> AccountPasskeyView:
        assert account_id == "acc-1"
        return _view(has_passkey=has_passkey)

    monkeypatch.setattr(f"services.accounts.{service}", _fake)
    async with _client(app) as client:
        resp = await getattr(client, method)(url)

    assert resp.status_code == 200
    assert resp.json() == _view(has_passkey=has_passkey).model_dump()


@pytest.mark.parametrize(("query", "forget_only"), [("", False), ("?forget_only=true", True)])
@pytest.mark.asyncio
async def test_removal_passes_forget_only_through(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    forget_only: bool,  # noqa: FBT001 - parametrized
) -> None:
    seen: list[bool] = []

    async def _remove(_account_id: str, *, forget_only: bool) -> AccountPasskeyView:
        seen.append(forget_only)
        return _view(has_passkey=False)

    monkeypatch.setattr("services.accounts.remove_account_passkey", _remove)
    async with _client(app) as client:
        resp = await client.delete(_URL + query)

    assert resp.status_code == 200
    assert seen == [forget_only]


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


@pytest.mark.parametrize(
    ("state", "reason"),
    [("present", None), ("missing", None), ("unknown", "unavailable")],
)
@pytest.mark.asyncio
async def test_the_check_route_answers_the_telegram_state(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    reason: str | None,
) -> None:
    async def _check(account_id: str) -> AccountPasskeyCheck:
        assert account_id == "acc-1"
        return AccountPasskeyCheck(
            account_id="acc-1",
            telegram_state=state,  # ty: ignore[invalid-argument-type]
            reason=reason,  # ty: ignore[invalid-argument-type]
        )

    monkeypatch.setattr("services.accounts.check_account_passkey", _check)
    async with _client(app) as client:
        resp = await client.post(f"{_URL}/check")

    assert resp.status_code == 200
    assert resp.json() == {"account_id": "acc-1", "telegram_state": state, "reason": reason}


@pytest.mark.parametrize(
    ("error", "status", "fields"),
    [
        (AccountNotFoundError("acc-1"), 404, None),
        (AccountActionError("passkey_missing"), 400, None),
        (
            AccountActionError("flood_wait", retry_after_seconds=60),
            400,
            {"retry_after_seconds": "60"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_the_check_route_maps_refusals(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status: int,
    fields: dict[str, str] | None,
) -> None:
    async def _refused(_account_id: str) -> AccountPasskeyCheck:
        raise error

    monkeypatch.setattr("services.accounts.check_account_passkey", _refused)
    async with _client(app) as client:
        resp = await client.post(f"{_URL}/check")

    assert resp.status_code == status
    assert resp.json()["error"].get("fields") == fields
