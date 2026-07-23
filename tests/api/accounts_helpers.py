"""Shared helpers for account API tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from schemas.accounts import AccountRead

if TYPE_CHECKING:
    from fastapi import FastAPI


def account(account_id: str = "acc-1") -> AccountRead:
    return AccountRead(account_id=account_id, status="alive", created_at="now", updated_at="now")


def client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")
