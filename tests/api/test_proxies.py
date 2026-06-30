"""Proxy-pool endpoint tests — thin routes over the real pool service."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from core.db import create_account
from schemas.accounts import AccountCreate
from schemas.proxy import ProxyCheckResult

if TYPE_CHECKING:
    from fastapi import FastAPI


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _create_proxy(client: httpx.AsyncClient, *, host: str = "h", port: int = 1080) -> str:
    resp = await client.post(
        "/api/v1/proxies",
        json={"proxy_type": "socks5", "host": host, "port": port},
    )
    assert resp.status_code == 200
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_list_proxies_starts_empty(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.get("/api/v1/proxies")
    assert resp.status_code == 200
    assert resp.json() == {"proxies": []}


@pytest.mark.asyncio
async def test_create_proxy_returns_pool_entry(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/proxies",
            json={"proxy_type": "socks5", "host": "nl.example", "port": 1080},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["host"] == "nl.example"
    assert body["used"] == 0
    assert body["capacity"] == 3


@pytest.mark.asyncio
async def test_assign_fills_a_slot(app: FastAPI) -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    async with _client(app) as client:
        proxy_id = await _create_proxy(client)
        resp = await client.post(
            f"/api/v1/proxies/{proxy_id}/assign",
            json={"account_id": "acc-1"},
        )
    assert resp.status_code == 200
    assert resp.json()["used"] == 1


@pytest.mark.asyncio
async def test_assign_over_capacity_is_409(app: FastAPI) -> None:
    for index in range(3):
        await create_account(AccountCreate(account_id=f"acc-{index}"))
    await create_account(AccountCreate(account_id="acc-overflow"))
    async with _client(app) as client:
        proxy_id = await _create_proxy(client)
        for index in range(3):
            await client.post(
                f"/api/v1/proxies/{proxy_id}/assign",
                json={"account_id": f"acc-{index}"},
            )
        resp = await client.post(
            f"/api/v1/proxies/{proxy_id}/assign",
            json={"account_id": "acc-overflow"},
        )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_assign_unknown_account_is_404(app: FastAPI) -> None:
    async with _client(app) as client:
        proxy_id = await _create_proxy(client)
        resp = await client.post(
            f"/api/v1/proxies/{proxy_id}/assign",
            json={"account_id": "ghost"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_check_proxy_persists_geo(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_check(_proxy: object) -> ProxyCheckResult:
        return ProxyCheckResult(status="tcp_working", exit_ip="1.2.3.4", country_code="NL")

    monkeypatch.setattr("services.proxies.check_proxy_connectivity", fake_check)
    async with _client(app) as client:
        proxy_id = await _create_proxy(client)
        resp = await client.post(f"/api/v1/proxies/{proxy_id}/check")
    assert resp.status_code == 200
    assert resp.json()["country_code"] == "NL"


@pytest.mark.asyncio
async def test_check_unknown_proxy_is_404(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/v1/proxies/missing/check")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_unassign_and_delete(app: FastAPI) -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    async with _client(app) as client:
        proxy_id = await _create_proxy(client)
        await client.post(f"/api/v1/proxies/{proxy_id}/assign", json={"account_id": "acc-1"})

        unassign = await client.post("/api/v1/proxies/unassign", json={"account_id": "acc-1"})
        assert unassign.status_code == 204

        delete = await client.delete(f"/api/v1/proxies/{proxy_id}")
        assert delete.status_code == 204

        pool = await client.get("/api/v1/proxies")
    assert pool.json() == {"proxies": []}


@pytest.mark.asyncio
async def test_probe_proxy_does_not_persist(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_check(_proxy: object) -> ProxyCheckResult:
        return ProxyCheckResult(status="tcp_working", country_code="DE")

    monkeypatch.setattr("services.proxies.check_proxy_connectivity", fake_check)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/proxies/probe",
            json={"proxy_type": "https", "host": "h", "port": 8080},
        )
        pool = await client.get("/api/v1/proxies")
    assert resp.status_code == 200
    assert resp.json()["country_code"] == "DE"
    assert pool.json() == {"proxies": []}
