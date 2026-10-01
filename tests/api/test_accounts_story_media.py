"""Story full-media endpoint tests (the profile modal's story player)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from schemas.profile_media import ProfileImage
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.mark.asyncio
async def test_story_media_serves_video_with_cache_headers(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str, story_id: int) -> ProfileImage | None:
        assert account_id == "acc-1"
        assert story_id == 9
        return ProfileImage(content=b"mp4-bytes", media_type="video/mp4", etag="v1")

    monkeypatch.setattr("services.accounts.account_story_media", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/acc-1/profile/stories/9/media")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.headers["etag"] == "v1"
    assert resp.content == b"mp4-bytes"


@pytest.mark.asyncio
async def test_story_media_returns_304_on_matching_etag(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str, story_id: int) -> ProfileImage | None:  # noqa: ARG001
        return ProfileImage(content=b"mp4-bytes", media_type="video/mp4", etag="v1")

    monkeypatch.setattr("services.accounts.account_story_media", _fake)
    async with _client(app) as client:
        resp = await client.get(
            "/api/v1/accounts/acc-1/profile/stories/9/media",
            headers={"If-None-Match": "v1"},
        )
    assert resp.status_code == 304


@pytest.mark.asyncio
async def test_story_media_unavailable_is_404(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str, story_id: int) -> ProfileImage | None:  # noqa: ARG001
        return None

    monkeypatch.setattr("services.accounts.account_story_media", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/acc-1/profile/stories/9/media")
    assert resp.status_code == 404
