"""``POST /accounts/{id}/session/restore`` and ``GET /accounts/{id}/session/copies``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from schemas.accounts import AccountCreate
from services.accounts import add_account
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.mark.asyncio
async def test_copies_of_an_account_with_no_vault(app: FastAPI) -> None:
    await add_account(AccountCreate(account_id="a1", session_name="a1"))
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/a1/session/copies")
    assert resp.status_code == 200
    assert resp.json() == {
        "vault_enabled": False,
        "count": 0,
        "newest_at": None,
        "can_restore": False,
    }


@pytest.mark.asyncio
async def test_restore_without_the_vault_is_a_stable_400_code(app: FastAPI) -> None:
    await add_account(AccountCreate(account_id="a1", session_name="a1"))
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/a1/session/restore")
    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "session_vault_off"


@pytest.mark.asyncio
async def test_restore_for_a_missing_account_is_404(app: FastAPI) -> None:
    async with _client(app) as client:
        assert (await client.post("/api/v1/accounts/ghost/session/restore")).status_code == 404
        assert (await client.get("/api/v1/accounts/ghost/session/copies")).status_code == 404
