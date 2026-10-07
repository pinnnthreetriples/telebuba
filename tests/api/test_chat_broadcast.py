"""``/chat-broadcast`` routes — which refusal is which status, against the real service."""

from __future__ import annotations

import asyncio
import io
from typing import TYPE_CHECKING

import httpx
import pytest
from PIL import Image

from core.config import settings
from core.repositories import chat_broadcast as repository
from services import _account_owner
from services.chat_broadcast import _runtime, _seams, _state
from tests.services.chat_broadcast.fakes import Clock, FakeTelegram, seed

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fastapi import FastAPI

_BASE = "/api/v1/chat-broadcast"


@pytest.fixture(autouse=True)
def telegram(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[FakeTelegram]:
    _account_owner.reset_for_tests()
    _state.reset_for_tests()
    _runtime.reset_for_tests()
    clock = Clock()
    fake = FakeTelegram(clock=clock)

    async def _sleep(seconds: float) -> None:
        clock.advance(seconds)
        await asyncio.sleep(0)

    async def _slot(*_args: object) -> None:
        return None

    monkeypatch.setattr(_seams, "execute", fake.execute)
    monkeypatch.setattr(_seams, "execute_read", fake.execute_read)
    monkeypatch.setattr(_seams, "solve_challenge", fake.solve_challenge)
    monkeypatch.setattr(_seams, "sleep", _sleep)
    monkeypatch.setattr(_seams, "now", clock.now)
    monkeypatch.setattr(_seams, "await_send_slot", _slot)
    monkeypatch.setattr(settings.chat_broadcast, "media_dir", tmp_path / "media")
    yield fake
    _account_owner.reset_for_tests()


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_campaign_crud_settings_and_board(app: FastAPI) -> None:
    async with _client(app) as client:
        created = (await client.post(f"{_BASE}/campaigns", json={"name": "Crypto"})).json()
        cid = created["campaign_id"]
        settings_read = (await client.get(f"{_BASE}/campaigns/{cid}/settings")).json()
        saved = await client.put(
            f"{_BASE}/campaigns/{cid}/settings",
            json={
                "expected_updated_at": settings_read["updated_at"],
                "name": "Crypto chats",
                "account_ids": [],
                "settings": {"targets": ["@alpha"], "messages": [{"text": "Hi"}]},
            },
        )
        stale = await client.put(
            f"{_BASE}/campaigns/{cid}/settings",
            json={
                "expected_updated_at": settings_read["updated_at"],
                "name": "x",
                "settings": {},
            },
        )
        invalid = await client.put(
            f"{_BASE}/campaigns/{cid}/settings",
            json={
                "expected_updated_at": saved.json()["updated_at"],
                "name": "x",
                "settings": {"targets": ["no way"]},
            },
        )
        board = await client.get(f"{_BASE}/campaigns/{cid}/board")
        listed = await client.get(f"{_BASE}/campaigns")
        missing = await client.get(f"{_BASE}/campaigns/nope/board")
        deleted = await client.delete(f"{_BASE}/campaigns/{cid}")
        gone = await client.delete(f"{_BASE}/campaigns/{cid}")

    assert saved.status_code == 200
    assert saved.json()["name"] == "Crypto chats"
    assert (stale.status_code, stale.json()["error"]["message"]) == (409, "campaign_changed")
    assert (invalid.status_code, invalid.json()["error"]["message"]) == (400, "invalid_target")
    assert board.json()["phase"] == "draft"
    assert [c["name"] for c in listed.json()["items"]] == ["Crypto chats"]
    assert missing.status_code == 404
    assert (deleted.status_code, gone.status_code) == (204, 404)
    assert (await client_get_settings_missing(app)) == 404


async def client_get_settings_missing(app: FastAPI) -> int:
    async with _client(app) as client:
        return (await client.get(f"{_BASE}/campaigns/nope/settings")).status_code


async def _targets_laid_out(campaign_id: str) -> None:
    """The run task lays the chats out in the background; wait for it."""
    for _attempt in range(200):
        if await repository.list_targets(campaign_id):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(campaign_id)


@pytest.mark.asyncio
async def test_start_stop_and_board_actions(app: FastAPI, telegram: FakeTelegram) -> None:
    cid = await seed(accounts=("a1",), targets=("@alpha",), messages=("Hi",))
    telegram.hold = asyncio.Event()
    async with _client(app) as client:
        stale = await client.post(
            f"{_BASE}/campaigns/{cid}/start",
            json={"expected_updated_at": "2020-01-01T00:00:00+00:00"},
        )
        settings_read = (await client.get(f"{_BASE}/campaigns/{cid}/settings")).json()
        started = await client.post(
            f"{_BASE}/campaigns/{cid}/start",
            json={"expected_updated_at": settings_read["updated_at"]},
        )
        twice = await client.post(
            f"{_BASE}/campaigns/{cid}/start",
            json={"expected_updated_at": settings_read["updated_at"]},
        )
        await _targets_laid_out(cid)
        skip = await client.post(
            f"{_BASE}/campaigns/{cid}/targets/action",
            json={"chat_key": "nope", "action": "skip"},
        )
        hand = await client.post(
            f"{_BASE}/campaigns/{cid}/targets/action",
            json={"chat_key": "alpha", "action": "hand", "account_id": "x"},
        )
        stopped = await client.post(f"{_BASE}/campaigns/{cid}/stop")
        telegram.hold.set()
        missing = await client.post(f"{_BASE}/campaigns/nope/stop")
        missing_start = await client.post(
            f"{_BASE}/campaigns/nope/start",
            json={"expected_updated_at": "2020-01-01T00:00:00+00:00"},
        )
        missing_action = await client.post(
            f"{_BASE}/campaigns/nope/targets/action",
            json={"chat_key": "alpha", "action": "skip"},
        )

    assert (stale.status_code, stale.json()["error"]["message"]) == (409, "campaign_changed")
    assert started.status_code == 200
    assert started.json()["campaign"]["status"] == "running"
    assert (twice.status_code, twice.json()["error"]["message"]) == (409, "campaign_running")
    assert (skip.status_code, skip.json()["error"]["message"]) == (409, "target_not_found")
    assert (hand.status_code, hand.json()["error"]["message"]) == (
        400,
        "account_not_in_campaign",
    )
    assert stopped.json()["campaign"]["status"] == "stopped"
    assert (missing.status_code, missing_start.status_code, missing_action.status_code) == (
        404,
        404,
        404,
    )


@pytest.mark.asyncio
async def test_resolve_own_chats_and_photo_upload(app: FastAPI) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2)).save(buffer, format="PNG")
    async with _client(app) as client:
        resolved = await client.post(
            f"{_BASE}/targets/resolve", json={"targets": ["@alpha", "!!bad"]}
        )
        own = await client.get(f"{_BASE}/own-chats", params={"account_ids": ["a1"]})
        uploaded = await client.post(
            f"{_BASE}/media", files={"file": ("bot.png", buffer.getvalue(), "image/png")}
        )
        wrong = await client.post(f"{_BASE}/media", files={"file": ("x.gif", b"GIF", "image/gif")})

    assert [item["kind"] for item in resolved.json()["items"]] == ["public", None]
    assert own.json()["groups"] == []
    assert uploaded.status_code == 200
    assert uploaded.json()["name"] == "bot.png"
    assert uploaded.json()["media_id"].endswith(".png")
    assert (wrong.status_code, wrong.json()["error"]["message"]) == (400, "media_invalid")


@pytest.mark.asyncio
async def test_an_oversized_photo_is_refused_before_it_is_read(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.profile_media, "photo_max_bytes", 4)
    async with _client(app) as client:
        response = await client.post(
            f"{_BASE}/media", files={"file": ("a.png", b"123456789", "image/png")}
        )

    assert response.status_code == 400
