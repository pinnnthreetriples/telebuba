"""The story ``design``: a multipart JSON field when posting now, a JSON body when scheduling."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from PIL import Image

from core.db import create_account
from schemas.accounts import AccountCreate
from schemas.profile_media import AccountStoryUpload, StoryPostResult
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI

_DESIGN: dict[str, Any] = {
    "caption": "Hello \U0001f393 world",
    "entities": [{"type": "bold", "offset": 8, "length": 5}],
    "period_hours": 48,
    "pinned": True,
    "protect_content": True,
    "privacy_preset": "public",
    "link_stickers": [
        {
            "url": "example.com",
            "label": "Shop",
            "style": "accent",
            "x": 30,
            "y": 70,
            "scale": 1.5,
            "rotation": -10,
        }
    ],
}


def _capture(monkeypatch: pytest.MonkeyPatch, *, downgraded: list[str] | None = None) -> list[Any]:
    seen: list[AccountStoryUpload] = []

    async def _fake(upload: AccountStoryUpload) -> StoryPostResult:
        seen.append(upload)
        return StoryPostResult.model_validate(
            {
                "status": "ok",
                "action_type": "post_story",
                "account_id": "acc-1",
                "message_id": 42,
                "downgraded": downgraded or [],
            }
        )

    monkeypatch.setattr("services.accounts.post_account_story", _fake)
    return seen


async def _post(app: FastAPI, **data: str) -> Any:
    async with _client(app) as client:
        return await client.post(
            "/api/v1/accounts/acc-1/story",
            files={"files": ("s.jpg", b"img", "image/jpeg")},
            data={"media_kind": "image", **data},
        )


@pytest.mark.asyncio
async def test_the_design_json_reaches_the_service_as_a_model(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _capture(monkeypatch)

    response = await _post(app, design=json.dumps(_DESIGN))

    assert response.status_code == 200, response.text
    design = seen[0].design
    assert design is not None
    assert (design.period_hours, design.pinned, design.protect_content) == (48, True, True)
    assert design.privacy_preset == "public"
    assert [e.type for e in design.entities] == ["bold"]
    sticker = design.link_stickers[0]
    assert (sticker.url, sticker.style, sticker.rotation) == ("https://example.com", "accent", -10)


@pytest.mark.asyncio
async def test_the_response_carries_what_a_free_account_lost(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capture(monkeypatch, downgraded=["period", "link_stickers"])

    response = await _post(app, design=json.dumps(_DESIGN))

    body = response.json()
    assert (body["status"], body["message_id"]) == ("ok", 42)
    assert body["downgraded"] == ["period", "link_stickers"]


@pytest.mark.asyncio
async def test_the_flat_fields_still_work_without_a_design(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _capture(monkeypatch)

    response = await _post(
        app, caption="flat", privacy_preset="close_friends", protect_content="true"
    )

    assert response.status_code == 200
    upload = seen[0]
    assert upload.design is None
    assert (upload.caption, upload.privacy_preset, upload.protect_content) == (
        "flat",
        "close_friends",
        True,
    )
    assert response.json()["downgraded"] == []


@pytest.mark.asyncio
async def test_a_blank_design_field_is_ignored(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _capture(monkeypatch)

    response = await _post(app, design="  ")

    assert response.status_code == 200
    assert seen[0].design is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "design",
    [
        "{not json",
        json.dumps({"period_hours": 7}),
        json.dumps(
            {"link_stickers": [{"url": "javascript:alert(1)", "label": "x", "x": 1, "y": 1}]}
        ),
        json.dumps({"caption": "hi", "entities": [{"type": "bold", "offset": 1, "length": 5}]}),
        json.dumps({"entities": [{"type": "text_url", "offset": 0, "length": 1}]}),
        json.dumps({"link_stickers": [{"url": "example.com", "label": "x", "x": 101, "y": 1}]}),
        json.dumps({"link_stickers": [{"url": "a.co", "label": "x" * 41, "x": 1, "y": 1}]}),
    ],
)
async def test_an_invalid_design_is_a_400_with_a_stable_code(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, design: str
) -> None:
    seen = _capture(monkeypatch)

    response = await _post(app, design=design)

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "story_design_invalid"
    assert seen == []


@pytest.mark.asyncio
async def test_a_caption_up_to_2048_is_accepted_in_the_flat_field(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _capture(monkeypatch)

    ok = await _post(app, caption="x" * 2048)
    too_long = await _post(app, caption="x" * 2049)

    assert ok.status_code == 200
    assert seen[0].caption == "x" * 2048
    assert too_long.status_code == 422


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "red").save(buffer, format="PNG")
    return buffer.getvalue()


def _at(minutes: float) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


@pytest.mark.asyncio
async def test_a_scheduled_story_takes_the_design_and_lists_it(app: FastAPI) -> None:
    await create_account(AccountCreate(account_id="acc"))
    async with _client(app) as http:
        uploaded = await http.post(
            "/api/v1/scheduled/media", files={"file": ("me.png", _png(), "image/png")}
        )
        media_id = uploaded.json()["media_id"]
        scheduled = await http.post(
            "/api/v1/accounts/acc/scheduled/story",
            json={"media_ids": [media_id], "run_at": _at(30), "design": _DESIGN},
        )
        listed = await http.get("/api/v1/accounts/acc/scheduled")

    assert scheduled.status_code == 200, scheduled.text
    body = scheduled.json()
    assert (body["caption"], body["privacy_preset"]) == ("Hello \U0001f393 world", "public")
    assert (body["period_hours"], body["pinned"], body["link_sticker_count"]) == (48, True, 1)
    assert body["downgraded"] == []
    assert listed.json()["items"][0]["downgraded"] == []


@pytest.mark.asyncio
async def test_a_scheduled_design_that_does_not_validate_is_a_422(app: FastAPI) -> None:
    await create_account(AccountCreate(account_id="acc"))
    async with _client(app) as http:
        uploaded = await http.post(
            "/api/v1/scheduled/media", files={"file": ("me.png", _png(), "image/png")}
        )
        response = await http.post(
            "/api/v1/accounts/acc/scheduled/story",
            json={
                "media_ids": [uploaded.json()["media_id"]],
                "run_at": _at(30),
                "design": {"period_hours": 7},
            },
        )

    assert response.status_code == 422
