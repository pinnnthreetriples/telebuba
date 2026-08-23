"""Listener-pointer endpoint tests.

A separate module from ``test_neurocomment.py``, which is already close to the
700-line test cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from schemas.neurocomment import NeurocommentRuntimeStatus

if TYPE_CHECKING:
    from fastapi import FastAPI


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _post_listener(app: FastAPI) -> httpx.Response:
    async with _client(app) as client:
        return await client.post(
            "/api/v1/neurocomment/listener",
            json={"listener_account_id": "acc-2"},
        )


@pytest.mark.asyncio
async def test_set_listener_persists_without_starting(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /listener remembers the pick and leaves the stopped engine stopped."""
    picked: list[str] = []

    async def _remember(account_id: str) -> bool:
        picked.append(account_id)
        return True

    async def _status() -> NeurocommentRuntimeStatus:
        return NeurocommentRuntimeStatus(running=False, listener_account_id="acc-2", log_limit=50)

    monkeypatch.setattr("services.neurocomment.remember_neurocomment_listener", _remember)
    monkeypatch.setattr("services.neurocomment.neurocomment_runtime_status", _status)
    resp = await _post_listener(app)
    assert resp.status_code == 200
    assert picked == ["acc-2"]
    assert resp.json()["listener_account_id"] == "acc-2"


@pytest.mark.asyncio
async def test_set_listener_hands_a_running_engine_to_start(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refused by the service (engine running), so the route re-points through /start."""
    started: list[str] = []

    async def _remember(_account_id: str) -> bool:
        return False

    async def _start(account_id: str) -> None:
        started.append(account_id)

    async def _status() -> NeurocommentRuntimeStatus:
        return NeurocommentRuntimeStatus(running=True, listener_account_id="acc-2", log_limit=50)

    monkeypatch.setattr("services.neurocomment.remember_neurocomment_listener", _remember)
    monkeypatch.setattr("services.neurocomment.start_neurocomment", _start)
    monkeypatch.setattr("services.neurocomment.neurocomment_runtime_status", _status)
    resp = await _post_listener(app)
    assert resp.status_code == 200
    assert started == ["acc-2"]
