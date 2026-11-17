"""The appeal routes: open the dialog, say it was filed, read the record (design 5.12)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from api import create_app
from core.db import upsert_spam_status
from schemas.accounts import AccountCreate, OpenWebResult
from schemas.spam_status import SpamStatusVerdict
from services.accounts import add_account
from services.accounts.web_login import NoProxyForWebLoginError
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI

_NOW = datetime.now(UTC).isoformat()
_BASE = "/api/v1/accounts"


async def _limited(account_id: str = "acc-1", detail: str | None = None) -> None:
    await add_account(AccountCreate(account_id=account_id))
    await upsert_spam_status(
        SpamStatusVerdict(account_id=account_id, status="limited", detail=detail, checked_at=_NOW)
    )


@pytest.mark.asyncio
async def test_an_account_nobody_appealed_for_reads_none(app: FastAPI) -> None:
    await add_account(AccountCreate(account_id="acc-1"))
    async with _client(app) as client:
        resp = await client.get(f"{_BASE}/acc-1/spam-appeal")

    assert resp.status_code == 200
    assert resp.json() == {
        "account_id": "acc-1",
        "status": "none",
        "filed_at": None,
        "filed_by": None,
        "filed_verdict": None,
        "filed_until_day": None,
        "closed_at": None,
        "closed_by_kind": None,
        "recheck_at": None,
    }


@pytest.mark.asyncio
async def test_filed_records_the_signed_in_operator_and_the_card_reads_it_back(
    app: FastAPI,
) -> None:
    await _limited()
    async with _client(app) as client:
        filed = await client.post(f"{_BASE}/acc-1/spam-appeal/filed")
        card = await client.get(f"{_BASE}/acc-1/spam-appeal")
        listed = await client.get(f"{_BASE}/spam-appeals")

    assert filed.status_code == 200
    body = filed.json()
    assert (body["status"], body["filed_by"], body["filed_verdict"]) == ("open", "admin", "limited")
    assert body["filed_at"] is not None
    assert body["recheck_at"] is not None
    assert card.json() == body
    assert listed.json() == {"items": [body]}


@pytest.mark.asyncio
async def test_pressing_filed_twice_keeps_the_first_record(app: FastAPI) -> None:
    await _limited()
    async with _client(app) as client:
        first = (await client.post(f"{_BASE}/acc-1/spam-appeal/filed")).json()
        again = (await client.post(f"{_BASE}/acc-1/spam-appeal/filed")).json()
        listed = (await client.get(f"{_BASE}/spam-appeals")).json()

    assert again == first
    assert len(listed["items"]) == 1


@pytest.mark.asyncio
async def test_a_body_sent_to_filed_is_ignored_and_never_stored(app: FastAPI) -> None:
    """There is no field that could carry text: whatever a client sends changes nothing."""
    await _limited()
    async with _client(app) as client:
        resp = await client.post(
            f"{_BASE}/acc-1/spam-appeal/filed",
            json={"text": "please unban me", "filed_by": "someone else"},
        )

    body = resp.json()
    assert resp.status_code == 200
    assert body["filed_by"] == "admin"
    assert "please unban me" not in resp.text


@pytest.mark.asyncio
async def test_a_clean_account_has_nothing_to_appeal(app: FastAPI) -> None:
    await add_account(AccountCreate(account_id="acc-1"))
    await upsert_spam_status(
        SpamStatusVerdict(account_id="acc-1", status="clean", detail=None, checked_at=_NOW)
    )
    async with _client(app) as client:
        resp = await client.post(f"{_BASE}/acc-1/spam-appeal/filed")
        card = await client.get(f"{_BASE}/acc-1/spam-appeal")

    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "spam_appeal_not_limited"
    assert card.json()["status"] == "none"


@pytest.mark.asyncio
async def test_open_names_the_spambot_target_and_returns_the_window_result(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str | None]] = []

    async def _fake(account_id: str, *, target: str | None = None) -> OpenWebResult:
        seen.append((account_id, target))
        return OpenWebResult(launched=True, signed_in=True)

    monkeypatch.setattr("services.accounts.open_account_web", _fake)
    await add_account(AccountCreate(account_id="acc-1"))
    async with _client(app) as client:
        resp = await client.post(f"{_BASE}/acc-1/spam-appeal/open")

    assert resp.status_code == 200
    assert resp.json()["launched"] is True
    assert resp.json()["signed_in"] is True
    assert seen == [("acc-1", "spambot")]


@pytest.mark.asyncio
async def test_open_without_a_proxy_is_refused_like_the_plain_open(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _refuse(account_id: str, *, target: str | None = None) -> OpenWebResult:  # noqa: ARG001
        refusal = "web_login_no_proxy"
        raise NoProxyForWebLoginError(refusal)

    monkeypatch.setattr("services.accounts.open_account_web", _refuse)
    await add_account(AccountCreate(account_id="acc-1"))
    async with _client(app) as client:
        resp = await client.post(f"{_BASE}/acc-1/spam-appeal/open")

    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "web_login_no_proxy"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/accounts/acc-nope/spam-appeal"),
        ("POST", "/accounts/acc-nope/spam-appeal/open"),
        ("POST", "/accounts/acc-nope/spam-appeal/filed"),
    ],
)
async def test_a_missing_account_is_a_404(app: FastAPI, method: str, path: str) -> None:
    async with _client(app) as client:
        resp = await client.request(method, f"/api/v1{path}")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/accounts/spam-appeals"),
        ("GET", "/accounts/acc-1/spam-appeal"),
        ("POST", "/accounts/acc-1/spam-appeal/open"),
        ("POST", "/accounts/acc-1/spam-appeal/filed"),
    ],
)
async def test_every_appeal_route_needs_a_session(method: str, path: str) -> None:
    async with _client(create_app()) as client:
        resp = await client.request(method, f"/api/v1{path}")

    assert resp.status_code == 401
