"""Logs endpoint tests — real rows seeded via log_event, cursor pagination + filters."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from core.logging import log_event

if TYPE_CHECKING:
    from fastapi import FastAPI


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_logs_returns_page_envelope(app: FastAPI) -> None:
    await log_event("INFO", "thing_happened", account_id="acc-1")
    async with _client(app) as client:
        resp = await client.get("/api/v1/logs")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "next_cursor"}
    assert body["items"][0]["event"] == "thing_happened"


@pytest.mark.asyncio
async def test_logs_paginate_with_cursor(app: FastAPI) -> None:
    for i in range(3):
        await log_event("INFO", f"event_{i}")
    async with _client(app) as client:
        first = await client.get("/api/v1/logs", params={"limit": 2})
        page1 = first.json()
        assert len(page1["items"]) == 2
        assert page1["next_cursor"] == "2"
        second = await client.get("/api/v1/logs", params={"limit": 2, "cursor": "2"})
    page2 = second.json()
    assert len(page2["items"]) == 1
    assert page2["next_cursor"] is None


@pytest.mark.asyncio
async def test_logs_filter_by_status(app: FastAPI) -> None:
    await log_event("INFO", "ok_event")
    await log_event("ERROR", "bad_event")
    async with _client(app) as client:
        resp = await client.get("/api/v1/logs", params={"status": "error"})
    events = [row["event"] for row in resp.json()["items"]]
    assert events == ["bad_event"]


@pytest.mark.asyncio
async def test_logs_filter_by_event_prefix(app: FastAPI) -> None:
    await log_event("INFO", "neurocomment_comment_posted")
    await log_event("INFO", "warming_subscribe")
    async with _client(app) as client:
        resp = await client.get("/api/v1/logs", params={"event_prefix": "neurocomment"})
    events = [row["event"] for row in resp.json()["items"]]
    assert events == ["neurocomment_comment_posted"]


@pytest.mark.asyncio
async def test_logs_invalid_cursor_is_400(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.get("/api/v1/logs", params={"cursor": "nope"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_clear_logs_by_prefix_deletes_and_returns_count(app: FastAPI) -> None:
    await log_event("INFO", "neurocomment_posted")
    await log_event("WARNING", "neurocomment_post_failed")
    await log_event("INFO", "warming_subscribe")
    async with _client(app) as client:
        resp = await client.request(
            "DELETE", "/api/v1/logs", params={"event_prefix": "neurocomment"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"deleted": 2}
        left = await client.get("/api/v1/logs")
    events = [row["event"] for row in left.json()["items"]]
    assert events == ["warming_subscribe"]
