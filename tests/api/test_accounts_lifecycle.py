"""Delete reason on ``DELETE /accounts/{id}`` and the dialog's pre-selected guess."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import update_account_from_session_check
from core.repositories.lifecycle import list_lifecycle_events
from schemas.accounts import AccountCreate
from schemas.telegram_session import TelegramSessionCheckResult
from services.accounts import add_account
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.mark.asyncio
async def test_the_guess_is_retired_for_a_healthy_account(app: FastAPI) -> None:
    await add_account(AccountCreate(account_id="a1"))
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/a1/delete-reason")
    assert resp.status_code == 200
    assert resp.json() == {"reason": "retired"}


@pytest.mark.asyncio
async def test_the_guess_is_dead_for_a_revoked_session(app: FastAPI) -> None:
    await add_account(AccountCreate(account_id="a1"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="a1", session_path="a1", status="unauthorized", is_temporary=False
        )
    )
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/a1/delete-reason")
    assert resp.json() == {"reason": "dead"}


@pytest.mark.asyncio
async def test_the_guess_for_a_missing_account_is_404(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/ghost/delete-reason")
    assert resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("?reason=dead", {"reason": "dead", "reason_source": "operator"}),
        ("?reason=retired", {"reason": "retired", "reason_source": "operator"}),
        ("", {"reason": "retired", "reason_source": "guess"}),
    ],
)
async def test_delete_journals_the_reason(
    app: FastAPI, query: str, expected: dict[str, object]
) -> None:
    await add_account(AccountCreate(account_id="a1"))
    async with _client(app) as client:
        resp = await client.delete(f"/api/v1/accounts/a1{query}")
    assert resp.status_code == 204
    [event] = await list_lifecycle_events("a1")
    assert (event.kind, event.extra) == ("deleted", expected)


@pytest.mark.asyncio
async def test_delete_refuses_an_unknown_reason(app: FastAPI) -> None:
    await add_account(AccountCreate(account_id="a1"))
    async with _client(app) as client:
        resp = await client.delete("/api/v1/accounts/a1?reason=bored")
    assert resp.status_code == 422
    assert await list_lifecycle_events("a1") == []
