"""Scheduled profile-post routes: upload once, schedule per account, move, cancel."""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from api import _large_upload_patterns
from core.db import create_account
from schemas.accounts import AccountCreate
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "red").save(buffer, format="PNG")
    return buffer.getvalue()


def _at(minutes: float) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


async def _upload(http: httpx.AsyncClient) -> str:
    response = await http.post(
        "/api/v1/scheduled/media",
        files={"file": ("me.png", _png(), "image/png")},
    )
    assert response.status_code == 200, response.text
    return response.json()["media_id"]


async def _schedule(http: httpx.AsyncClient, media_id: str) -> dict[str, object]:
    response = await http.post(
        "/api/v1/accounts/acc/scheduled/photo",
        json={"media_id": media_id, "run_at": _at(30), "filename": "me.png"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_only_the_media_upload_is_on_the_large_upload_allowlist() -> None:
    patterns = [re.compile(pattern) for pattern in _large_upload_patterns()]

    def allowed(path: str) -> bool:
        return any(pattern.fullmatch(path) for pattern in patterns)

    assert allowed("/api/v1/scheduled/media")
    assert not allowed("/api/v1/accounts/acc/scheduled/photo")
    assert not allowed("/api/v1/accounts/acc/scheduled/story")


@pytest.mark.asyncio
async def test_upload_schedule_list_move_and_cancel(app: FastAPI) -> None:
    await create_account(AccountCreate(account_id="acc"))
    async with _client(app) as http:
        media_id = await _upload(http)
        post = await _schedule(http, media_id)
        listed = await http.get("/api/v1/accounts/acc/scheduled")
        moved = await http.patch(
            f"/api/v1/accounts/acc/scheduled/{post['post_id']}",
            json={"run_at": _at(90)},
        )
        thumb = await http.get(str(post["thumb_url"]))
        cancelled = await http.delete(f"/api/v1/accounts/acc/scheduled/{post['post_id']}")
        again = await http.delete(f"/api/v1/accounts/acc/scheduled/{post['post_id']}")

    assert (post["state"], post["kind"], post["media_kind"]) == ("pending", "photo", "image")
    assert [item["post_id"] for item in listed.json()["items"]] == [post["post_id"]]
    assert "server_now" in listed.json()
    assert moved.status_code == 200
    assert moved.json()["run_at"] != post["run_at"]
    assert (thumb.status_code, thumb.headers["content-type"]) == (200, "image/jpeg")
    assert cancelled.json()["state"] == "cancelled"
    assert again.status_code == 409
    assert again.json()["error"]["message"] == "scheduled_not_reschedulable"


@pytest.mark.asyncio
async def test_a_story_is_scheduled_from_uploaded_media(app: FastAPI) -> None:
    await create_account(AccountCreate(account_id="acc"))
    async with _client(app) as http:
        media_id = await _upload(http)
        response = await http.post(
            "/api/v1/accounts/acc/scheduled/story",
            json={
                "media_ids": [media_id, media_id],
                "run_at": _at(30),
                "caption": "hello",
                "privacy_preset": "public",
                "collage_layout": "v2",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["kind"], body["media_count"], body["caption"]) == ("story", 2, "hello")


@pytest.mark.asyncio
async def test_refusals_carry_stable_codes(app: FastAPI) -> None:
    await create_account(AccountCreate(account_id="acc"))
    async with _client(app) as http:
        media_id = await _upload(http)
        too_soon = await http.post(
            "/api/v1/accounts/acc/scheduled/photo",
            json={"media_id": media_id, "run_at": _at(-10)},
        )
        naive = await http.post(
            "/api/v1/accounts/acc/scheduled/photo",
            json={"media_id": media_id, "run_at": "2030-01-01T10:00:00"},
        )
        not_stored = await http.post(
            "/api/v1/accounts/acc/scheduled/photo",
            json={"media_id": "f" * 64 + ".png", "run_at": _at(30)},
        )
        bad_file = await http.post(
            "/api/v1/scheduled/media",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )

    assert (too_soon.status_code, too_soon.json()["error"]["message"]) == (
        400,
        "scheduled_run_at_out_of_range",
    )
    assert naive.status_code == 422
    assert not_stored.json()["error"]["message"] == "scheduled_media_missing"
    assert bad_file.json()["error"]["message"] == "scheduled_media_invalid"


@pytest.mark.asyncio
async def test_unknown_accounts_and_posts_are_not_found(app: FastAPI) -> None:
    await create_account(AccountCreate(account_id="acc"))
    async with _client(app) as http:
        media_id = await _upload(http)
        ghost_account = await http.post(
            "/api/v1/accounts/ghost/scheduled/photo",
            json={"media_id": media_id, "run_at": _at(30)},
        )
        ghost_post = await http.delete("/api/v1/accounts/acc/scheduled/nope")
        ghost_thumb = await http.get("/api/v1/accounts/acc/scheduled/nope/thumb")
        ghost_list = await http.get("/api/v1/accounts/ghost/scheduled")

    assert ghost_account.status_code == 404
    assert ghost_post.status_code == 404
    assert ghost_post.json()["error"]["message"] == "scheduled_post_not_found"
    assert ghost_thumb.status_code == 404
    assert ghost_list.status_code == 404
