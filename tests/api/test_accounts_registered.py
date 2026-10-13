"""``PUT /accounts/{id}/registered-at`` and the optional date at upload (item 4.8)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.db import create_account, fetch_account
from schemas.accounts import AccountCreate, AccountRead
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI

_URL = "/api/v1/accounts/acc-1/registered-at"


@pytest.mark.asyncio
async def test_the_operator_sets_and_clears_the_registration_date(app: FastAPI) -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    async with _client(app) as client:
        resp = await client.put(_URL, json={"registered_at": "2021-03-04"})
        assert resp.status_code == 200
        assert resp.json()["telegram_registered_at"] == "2021-03-04"
        assert resp.json()["registered_source"] == "operator"

        cleared = await client.put(_URL, json={"registered_at": None})
    assert cleared.status_code == 200
    stored = await fetch_account("acc-1")
    assert stored is not None
    assert stored.telegram_registered_at is None
    assert stored.registered_source is None


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["2012-01-01", "future"])
async def test_a_date_outside_telegrams_lifetime_is_refused(app: FastAPI, value: str) -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    if value == "future":
        value = (datetime.now(UTC).date() + timedelta(days=2)).isoformat()
    async with _client(app) as client:
        resp = await client.put(_URL, json={"registered_at": value})
    assert resp.status_code == 422
    stored = await fetch_account("acc-1")
    assert stored is not None
    assert stored.telegram_registered_at is None


@pytest.mark.asyncio
async def test_an_unknown_account_is_404(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.put(_URL, json={"registered_at": "2021-03-04"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_the_session_upload_forwards_the_optional_date(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[object] = []

    async def _fake(data: object) -> AccountRead:
        seen.append(getattr(data, "registered_at", None))
        now = datetime.now(UTC).isoformat()
        return AccountRead(account_id="s", status="new", created_at=now, updated_at=now)

    monkeypatch.setattr("services.accounts.import_account_session", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/import-session",
            files={"file": ("acc.session", b"session-bytes", "application/octet-stream")},
            data={"registered_at": "2020-05-06"},
        )
    assert resp.status_code == 200
    assert str(seen[0]) == "2020-05-06"


@pytest.mark.asyncio
async def test_the_date_is_written_with_the_account_itself() -> None:
    created = await create_account(
        AccountCreate(account_id="acc-2", registered_at=date(2020, 5, 6))
    )
    assert created.telegram_registered_at == "2020-05-06"
    assert created.registered_source == "operator"
