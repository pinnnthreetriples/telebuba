"""The Accounts page's web-window count and its close-all action."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.mark.asyncio
async def test_the_open_window_count_is_served(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake() -> int:
        return 3

    monkeypatch.setattr("services.accounts.count_open_web_windows", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/web-windows")
    assert resp.status_code == 200
    assert resp.json() == {"count": 3}


@pytest.mark.asyncio
async def test_close_all_reports_how_many_windows_it_closed(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake() -> int:
        return 2

    monkeypatch.setattr("services.accounts.close_all_web_windows", _fake)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/web-windows/close")
    assert resp.status_code == 200
    assert resp.json() == {"count": 2}
