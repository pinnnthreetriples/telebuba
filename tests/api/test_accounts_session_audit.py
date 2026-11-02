"""Session audit endpoint tests — thin routes over mocked audit services."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from schemas.session_audit import AccountSession, AccountSessionAudit, AuthorizationList
from services.accounts import AccountActionError, AccountNotFoundError
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI

_URL = "/api/v1/accounts/acc-1/sessions"


def _audit() -> AccountSessionAudit:
    sessions = [
        AccountSession(
            hash=str(hash_),
            is_current=ours,
            is_ours=ours,
            official_app=False,
            device_model="Model",
            platform="Windows",
            system_version="10",
            app_name="Telegram Desktop",
            ip="192.0.2.10",
            country="Nowhere",
        )
        for hash_, ours in ((9007199254740993, True), (-5, False))
    ]
    return AccountSessionAudit.of(
        "acc-1", "2026-11-02T10:00:00+00:00", AuthorizationList(sessions=sessions, ttl_days=180)
    )


@pytest.mark.parametrize(
    ("method", "url", "service"),
    [
        ("get", _URL, "read_account_session_audit"),
        ("post", f"{_URL}/audit", "audit_account_sessions"),
    ],
)
@pytest.mark.asyncio
async def test_each_route_answers_the_service_audit(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, method: str, url: str, service: str
) -> None:
    async def _fake(account_id: str) -> AccountSessionAudit:
        assert account_id == "acc-1"
        return _audit()

    monkeypatch.setattr(f"services.accounts.{service}", _fake)
    async with _client(app) as client:
        resp = await getattr(client, method)(url)

    body = resp.json()
    assert resp.status_code == 200
    assert (body["total"], body["foreign_count"], body["ttl_days"]) == (2, 1, 180)
    # A 64-bit hash survives the wire as text; as a JSON number the browser would round it.
    assert [session["hash"] for session in body["sessions"]] == ["9007199254740993", "-5"]


@pytest.mark.asyncio
async def test_an_unknown_account_is_404(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _missing(account_id: str) -> AccountSessionAudit:
        raise AccountNotFoundError(account_id)

    monkeypatch.setattr("services.accounts.read_account_session_audit", _missing)
    async with _client(app) as client:
        resp = await client.get(_URL)

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_a_refusal_answers_its_stable_code_and_wait(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _refused(_account_id: str) -> AccountSessionAudit:
        code = "flood_wait"
        raise AccountActionError(code, retry_after_seconds=30)

    monkeypatch.setattr("services.accounts.audit_account_sessions", _refused)
    async with _client(app) as client:
        resp = await client.post(f"{_URL}/audit")

    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "flood_wait"
