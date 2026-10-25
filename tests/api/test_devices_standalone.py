"""Independent emulator routes: create, delete and move a phone bound to no account.

The pod side is faked on ``_standalone``'s names; the emulators and proxies are real rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from core.config import settings
from services.device_farm import _phones, _standalone

if TYPE_CHECKING:
    from fastapi import FastAPI

_ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def stopped(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Pod stops and removals the routes cause, by container name."""
    calls: list[str] = []

    async def stop_pod(name: str) -> None:
        calls.append(f"stop:{name}")

    async def remove_pod(name: str) -> bool:
        calls.append(f"remove:{name}")
        return True

    async def list_instances() -> list[object]:
        return []

    monkeypatch.setattr(settings.emulator, "enabled", True)
    monkeypatch.setattr(_phones, "list_instances", list_instances)
    monkeypatch.setattr(_standalone, "stop_pod", stop_pod)
    monkeypatch.setattr(_standalone, "remove_pod", remove_pod)
    return calls


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver", headers=_ORIGIN)


async def _proxy(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/api/v1/proxies", json={"proxy_type": "socks5", "host": "10.0.0.9", "port": 1080}
    )
    assert response.status_code == 200
    return response.json()["id"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("stopped")
async def test_a_created_emulator_is_listed_on_its_proxy_and_takes_a_slot(app: FastAPI) -> None:
    async with _client(app) as client:
        proxy_id = await _proxy(client)
        created = await client.post(
            "/api/v1/devices/standalone", json={"label": "DE-1", "proxy_id": proxy_id}
        )
        listed = await client.get("/api/v1/devices")
        pool = await client.get("/api/v1/proxies")

    assert created.status_code == 200
    phone = created.json()
    assert (phone["kind"], phone["label"], phone["proxy_id"], phone["apps"]) == (
        "standalone",
        "DE-1",
        proxy_id,
        [],
    )
    assert listed.json()["phones"] == [phone]
    assert pool.json()["proxies"][0]["used"] == 1
    assert phone["install_apps"] == ["telegram"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("stopped")
async def test_the_apps_picked_at_creation_are_kept(app: FastAPI) -> None:
    async with _client(app) as client:
        created = await client.post(
            "/api/v1/devices/standalone", json={"install_apps": ["gosuslugi", "telegram"]}
        )
        unknown = await client.post("/api/v1/devices/standalone", json={"install_apps": ["vk"]})
        listed = await client.get("/api/v1/devices")

    assert created.status_code == 200
    assert created.json()["install_apps"] == ["telegram", "gosuslugi"]
    assert unknown.status_code == 422
    assert [p["install_apps"] for p in listed.json()["phones"]] == [["telegram", "gosuslugi"]]


@pytest.mark.asyncio
async def test_an_unknown_proxy_answers_404(app: FastAPI) -> None:
    async with _client(app) as client:
        response = await client.post("/api/v1/devices/standalone", json={"proxy_id": "nope"})

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_full_proxy_answers_409(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.proxy, "max_accounts_per_proxy", 1)
    async with _client(app) as client:
        proxy_id = await _proxy(client)
        await client.post("/api/v1/devices/standalone", json={"proxy_id": proxy_id})
        second = await client.post("/api/v1/devices/standalone", json={})
        moved = await client.put(
            f"/api/v1/devices/standalone/{second.json()['phone_id']}/proxy",
            json={"proxy_id": proxy_id},
        )

    assert second.status_code == 200
    assert moved.status_code == 409


@pytest.mark.asyncio
async def test_the_proxy_is_changed_and_taken_off(app: FastAPI, stopped: list[str]) -> None:
    async with _client(app) as client:
        proxy_id = await _proxy(client)
        phone_id = (await client.post("/api/v1/devices/standalone", json={})).json()["phone_id"]
        assigned = await client.put(
            f"/api/v1/devices/standalone/{phone_id}/proxy", json={"proxy_id": proxy_id}
        )
        on_proxy = (await client.get("/api/v1/devices")).json()["phones"][0]["proxy_id"]
        removed = await client.delete(f"/api/v1/devices/standalone/{phone_id}/proxy")
        off_proxy = (await client.get("/api/v1/devices")).json()["phones"][0]["proxy_id"]

    assert (assigned.status_code, removed.status_code) == (204, 204)
    assert (on_proxy, off_proxy) == (proxy_id, None)
    assert stopped == [f"stop:{phone_id}"] * 2


@pytest.mark.asyncio
async def test_a_deleted_emulator_is_gone_with_its_pod(app: FastAPI, stopped: list[str]) -> None:
    async with _client(app) as client:
        phone_id = (await client.post("/api/v1/devices/standalone", json={})).json()["phone_id"]
        deleted = await client.delete(f"/api/v1/devices/standalone/{phone_id}")
        again = await client.delete(f"/api/v1/devices/standalone/{phone_id}")
        listed = await client.get("/api/v1/devices")

    assert deleted.status_code == 204
    assert again.status_code == 400
    assert again.json()["error"]["message"] == "emulator_not_found"
    assert listed.json()["phones"] == []
    assert stopped == [f"remove:{phone_id}"]
