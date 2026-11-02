"""Session audit endpoint tests — thin routes over mocked audit services."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from schemas.session_audit import AccountSession, AccountSessionAudit, AuthorizationList
from schemas.session_close import SessionCloseOutcome, SessionCloseResult
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


def _closed() -> SessionCloseResult:
    return SessionCloseResult(
        account_id="acc-1",
        outcomes=[
            SessionCloseOutcome(hash="9007199254740993", status="closed"),
            SessionCloseOutcome(hash="-5", status="too_new", retry_after_seconds=3600),
        ],
        closed_count=1,
        audit=_audit(),
    )


@pytest.mark.asyncio
async def test_closing_named_sessions_passes_the_hashes_and_answers_each_outcome(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    async def _close(account_id: str, hashes: list[str]) -> SessionCloseResult:
        assert account_id == "acc-1"
        seen.append(hashes)
        return _closed()

    monkeypatch.setattr("services.accounts.close_account_sessions", _close)
    async with _client(app) as client:
        resp = await client.post(f"{_URL}/close", json={"hashes": ["9007199254740993", "-5"]})

    body = resp.json()
    assert resp.status_code == 200
    assert seen == [["9007199254740993", "-5"]]
    assert [(o["hash"], o["status"]) for o in body["outcomes"]] == [
        ("9007199254740993", "closed"),
        ("-5", "too_new"),
    ]
    assert (body["closed_count"], body["audit"]["foreign_count"]) == (1, 1)


@pytest.mark.parametrize("hashes", [[], ["not-a-number"], [str(n) for n in range(51)]])
@pytest.mark.asyncio
async def test_a_list_of_hashes_that_is_empty_too_long_or_not_numeric_is_a_422(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, hashes: list[str]
) -> None:
    async def _never(_account_id: str, _hashes: list[str]) -> SessionCloseResult:
        raise AssertionError

    monkeypatch.setattr("services.accounts.close_account_sessions", _never)
    async with _client(app) as client:
        resp = await client.post(f"{_URL}/close", json={"hashes": hashes})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_closing_everything_foreign_takes_no_body(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _close(account_id: str) -> SessionCloseResult:
        assert account_id == "acc-1"
        return _closed()

    monkeypatch.setattr("services.accounts.close_account_foreign_sessions", _close)
    async with _client(app) as client:
        resp = await client.post(f"{_URL}/close-foreign")

    assert resp.status_code == 200
    assert resp.json()["closed_count"] == 1


@pytest.mark.asyncio
async def test_a_close_without_a_proxy_answers_its_stable_code(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _refused(_account_id: str) -> SessionCloseResult:
        code = "session_audit_proxy_missing"
        raise AccountActionError(code)

    monkeypatch.setattr("services.accounts.close_account_foreign_sessions", _refused)
    async with _client(app) as client:
        resp = await client.post(f"{_URL}/close-foreign")

    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "session_audit_proxy_missing"
