"""Account profile and media endpoint tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from schemas.profile_media import (
    AccountProfileMusicRemove,
    AccountProfilePhotoSetMain,
    AccountProfileView,
    AccountStoryPin,
    AccountStoryUpload,
    ProfileImage,
    ProfileMusicView,
    ProfilePhotoView,
    ProfileStoryView,
)
from schemas.telegram_actions import ActionResult
from services.accounts import AccountActionError
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.mark.asyncio
async def test_set_photo_accepts_multipart(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(upload: object) -> ActionResult:  # noqa: ARG001
        return ActionResult(status="ok", action_type="set_profile_photo", account_id="acc-1")

    monkeypatch.setattr("services.accounts.set_account_profile_photo", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/photo",
            files={"file": ("photo.jpg", b"img-bytes", "image/jpeg")},
            data={"account_id": "acc-1"},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_set_photo_value_error_is_400(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected photo (oversize / bad ext / failed action) surfaces as 400.

    ``set_account_profile_photo`` raises ``ValueError`` for an invalid upload or a
    failed Telegram action; the route must wrap it in the 400 envelope like its
    sibling upload routes, not let it become a 500.
    """

    async def _boom(upload: object) -> ActionResult:  # noqa: ARG001
        msg = "photo_too_large"
        raise ValueError(msg)

    monkeypatch.setattr("services.accounts.set_account_profile_photo", _boom)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/photo",
            files={"file": ("photo.jpg", b"img-bytes", "image/jpeg")},
            data={"account_id": "acc-1"},
        )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "bad_request"
    assert body["error"]["message"] == "photo_too_large"


@pytest.mark.asyncio
async def test_profile_snapshot_returns_view(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, bool] = {}

    async def _fake(account_id: str, *, force_refresh: bool = False) -> AccountProfileView:  # noqa: ARG001
        seen["force_refresh"] = force_refresh
        return AccountProfileView(
            first_name="Petr",
            username="petr_tg",
            photos=[ProfilePhotoView(photo_id="1", access_hash="2", file_reference="YWJj")],
            stories=[
                ProfileStoryView(story_id=5, kind="image", privacy_preset="contacts", views=42),
            ],
            music=[
                ProfileMusicView(file_id="7", title="T", access_hash="3", file_reference="YWJj"),
            ],
        )

    monkeypatch.setattr("services.accounts.account_profile_view", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/acc-1/profile-snapshot?refresh=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["first_name"] == "Petr"
    assert body["photos"][0]["photo_id"] == "1"
    assert body["stories"][0]["views"] == 42
    assert body["music"][0]["title"] == "T"
    assert seen["force_refresh"] is True  # the ?refresh=true query forwards to the service


@pytest.mark.asyncio
async def test_photo_thumb_returns_image_with_cache_headers(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str, *, kind: str, item_id: int) -> ProfileImage | None:
        assert account_id == "acc-1"
        assert kind == "photos"
        assert item_id == 1
        return ProfileImage(content=b"jpeg-bytes", etag="abc123")

    monkeypatch.setattr("services.accounts.account_profile_image", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/acc-1/profile/photos/1/thumb")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["etag"] == "abc123"
    assert resp.headers["cache-control"] == "private, max-age=3600, immutable"
    assert resp.content == b"jpeg-bytes"


@pytest.mark.asyncio
async def test_photo_thumb_returns_304_on_matching_etag(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str, *, kind: str, item_id: int) -> ProfileImage | None:  # noqa: ARG001
        return ProfileImage(content=b"jpeg-bytes", etag="abc123")

    monkeypatch.setattr("services.accounts.account_profile_image", _fake)
    async with _client(app) as client:
        resp = await client.get(
            "/api/v1/accounts/acc-1/profile/photos/1/thumb",
            headers={"If-None-Match": "abc123"},
        )
    assert resp.status_code == 304
    assert resp.content == b""


@pytest.mark.asyncio
async def test_photo_thumb_unknown_id_is_404(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str, *, kind: str, item_id: int) -> ProfileImage | None:  # noqa: ARG001
        return None

    monkeypatch.setattr("services.accounts.account_profile_image", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/acc-1/profile/photos/999/thumb")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_story_thumb_returns_image(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str, *, kind: str, item_id: int) -> ProfileImage | None:  # noqa: ARG001
        assert kind == "stories"
        assert item_id == 9
        return ProfileImage(content=b"story-bytes", etag="def456")

    monkeypatch.setattr("services.accounts.account_profile_image", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/acc-1/profile/stories/9/thumb")
    assert resp.status_code == 200
    assert resp.content == b"story-bytes"


@pytest.mark.asyncio
async def test_avatar_returns_image_with_cache_headers(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str) -> ProfileImage | None:
        assert account_id == "acc-1"
        return ProfileImage(content=b"avatar-bytes", etag="av123")

    monkeypatch.setattr("services.accounts.account_avatar_image", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/acc-1/avatar")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["etag"] == "av123"
    assert resp.headers["cache-control"] == "private, max-age=3600, immutable"
    assert resp.content == b"avatar-bytes"


@pytest.mark.asyncio
async def test_avatar_returns_304_on_matching_etag(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str) -> ProfileImage | None:  # noqa: ARG001
        return ProfileImage(content=b"avatar-bytes", etag="av123")

    monkeypatch.setattr("services.accounts.account_avatar_image", _fake)
    async with _client(app) as client:
        resp = await client.get(
            "/api/v1/accounts/acc-1/avatar",
            headers={"If-None-Match": "av123"},
        )
    assert resp.status_code == 304
    assert resp.content == b""


@pytest.mark.asyncio
async def test_avatar_missing_is_404(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str) -> ProfileImage | None:  # noqa: ARG001
        return None

    monkeypatch.setattr("services.accounts.account_avatar_image", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/acc-1/avatar")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_story_accepts_multipart(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(upload: object) -> ActionResult:  # noqa: ARG001
        return ActionResult(status="ok", action_type="post_story", account_id="acc-1")

    monkeypatch.setattr("services.accounts.post_account_story", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/story",
            files={"files": ("s.jpg", b"img", "image/jpeg")},
            data={"media_kind": "image", "privacy_preset": "contacts"},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_post_story_collage_multipart_reaches_service(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two+ files land on the service as image #1 + extra_images, with the layout."""
    seen: dict[str, object] = {}

    async def _fake(upload: AccountStoryUpload) -> ActionResult:
        seen["content"] = upload.content
        seen["extra_images"] = upload.extra_images
        seen["collage_layout"] = upload.collage_layout
        return ActionResult(status="ok", action_type="post_story", account_id="acc-1")

    monkeypatch.setattr("services.accounts.post_account_story", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/story",
            files=[
                ("files", ("a.jpg", b"first", "image/jpeg")),
                ("files", ("b.jpg", b"second", "image/jpeg")),
            ],
            data={"media_kind": "image", "collage_layout": "v2"},
        )
    assert resp.status_code == 200
    assert seen["content"] == b"first"
    assert seen["extra_images"] == [b"second"]
    assert seen["collage_layout"] == "v2"


@pytest.mark.asyncio
async def test_post_story_collage_count_cap_rejected_before_read(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Too many collage files are refused BEFORE buffering uploads into RAM.

    The service re-checks after decode; this API-level gate exists so N
    oversized uploads never get read into memory first. Same stable code on
    the wire as the service check.
    """
    monkeypatch.setattr(settings.profile_media, "story_collage_max_images", 2)

    async def _fake(upload: object) -> ActionResult:  # noqa: ARG001
        msg = "service must not be reached when the count cap fails"
        raise AssertionError(msg)

    monkeypatch.setattr("services.accounts.post_account_story", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/story",
            files=[
                ("files", ("a.jpg", b"1", "image/jpeg")),
                ("files", ("b.jpg", b"2", "image/jpeg")),
                ("files", ("c.jpg", b"3", "image/jpeg")),
            ],
            data={"media_kind": "image"},
        )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "bad_request"
    assert body["error"]["message"] == "story_collage_too_many_images"


@pytest.mark.asyncio
async def test_action_unavailable_maps_to_503(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``unavailable`` action code is a 503, not a 400 client fault.

    Pool/socket failures inside the gateway are an internal outage; billing
    them as ``bad_request`` blamed the operator's input for infra downtime.
    """

    async def _unavailable(data: object) -> ActionResult:  # noqa: ARG001
        code = "unavailable"
        raise AccountActionError(code)

    monkeypatch.setattr("services.accounts.set_account_story_pinned", _unavailable)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/story/pin",
            json={"story_id": 9, "pinned": True},
        )
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "unavailable"
    assert body["error"]["message"] == "unavailable"


@pytest.mark.asyncio
async def test_post_story_surfaces_video_error_code_not_russian(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A video-normalisation failure reaches the client as a stable code.

    ``normalize_story_video_for_telegram`` raises ``StoryVideoNormalisationError``
    whose ``str`` is a locale-neutral code; it flows through ``execute`` into the
    service's ``ValueError`` and out via the 400 envelope. The wire must carry the
    code, never the old Russian prose (non-negotiable #12).
    """

    async def _boom(upload: object) -> ActionResult:  # noqa: ARG001
        msg = "story_video_invalid"
        raise ValueError(msg)

    monkeypatch.setattr("services.accounts.post_account_story", _boom)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/story",
            files={"files": ("s.mp4", b"vid", "video/mp4")},
            data={"media_kind": "video", "privacy_preset": "contacts"},
        )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "bad_request"
    # The stable code is emitted; no Cyrillic / Russian prose crosses the wire.
    assert body["error"]["message"] == "story_video_invalid"
    assert not any("Ѐ" <= ch <= "ӿ" for ch in body["error"]["message"])


@pytest.mark.asyncio
async def test_post_story_surfaces_image_error_code_not_russian(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The image twin of the video regression above (non-negotiable #12).

    ``StoryImageNormalisationError`` flows through ``execute`` into the
    service's ``AccountActionError``; the wire must carry the stable code
    ``story_image_invalid``, never the old Russian prose.
    """

    async def _boom(upload: object) -> ActionResult:  # noqa: ARG001
        code = "story_image_invalid"
        raise AccountActionError(code)

    monkeypatch.setattr("services.accounts.post_account_story", _boom)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/story",
            files={"files": ("s.jpg", b"img", "image/jpeg")},
            data={"media_kind": "image", "privacy_preset": "contacts"},
        )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "bad_request"
    assert body["error"]["message"] == "story_image_invalid"
    assert not any("Ѐ" <= ch <= "ӿ" for ch in body["error"]["message"])
    assert "fields" not in body["error"]  # no retry seconds on a non-flood failure


@pytest.mark.asyncio
async def test_add_music_accepts_multipart(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(upload: object) -> ActionResult:  # noqa: ARG001
        return ActionResult(status="ok", action_type="add_profile_music", account_id="acc-1")

    monkeypatch.setattr("services.accounts.add_account_profile_music", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/music",
            files={"file": ("t.mp3", b"snd", "audio/mpeg")},
            data={"title": "Song"},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_remove_story(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(data: object) -> ActionResult:  # noqa: ARG001
        return ActionResult(status="ok", action_type="remove_story", account_id="acc-1")

    monkeypatch.setattr("services.accounts.remove_account_story", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/story/remove",
            json={"story_id": 9},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("pinned", [True, False])
async def test_set_story_pinned(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    *,
    pinned: bool,
) -> None:
    seen: dict[str, object] = {}

    async def _fake(data: AccountStoryPin) -> ActionResult:
        seen["story_id"] = data.story_id
        seen["pinned"] = data.pinned
        return ActionResult(status="ok", action_type="toggle_story_pinned", account_id="acc-1")

    monkeypatch.setattr("services.accounts.set_account_story_pinned", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/story/pin",
            json={"story_id": 9, "pinned": pinned},
        )
    assert resp.status_code == 200
    assert seen == {"story_id": 9, "pinned": pinned}


@pytest.mark.asyncio
async def test_remove_music_decodes_file_reference(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    async def _fake(data: AccountProfileMusicRemove) -> ActionResult:
        seen["ref"] = data.file_reference
        seen["file_id"] = data.file_id
        return ActionResult(status="ok", action_type="remove_profile_music", account_id="acc-1")

    monkeypatch.setattr("services.accounts.remove_account_profile_music", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/music/remove",
            # int64 ids travel as strings so the SPA can't round them past 2^53.
            json={"file_id": "9007199254740993", "access_hash": "3", "file_reference": "YWJj"},
        )
    assert resp.status_code == 200
    assert seen["ref"] == b"abc"  # base64 "YWJj" -> b"abc"
    assert seen["file_id"] == 9007199254740993  # survives past JS's safe-int limit


@pytest.mark.asyncio
async def test_remove_photo_bad_reference_is_400(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/photo/remove",
            json={"photo_id": "1", "access_hash": "2", "file_reference": "!!notbase64!!"},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_set_photo_main_preserves_int64_ids(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The set-main endpoint must decode the string ids back to full-precision int64."""
    seen: dict[str, object] = {}

    async def _fake(data: AccountProfilePhotoSetMain) -> ActionResult:
        seen["photo_id"] = data.photo_id
        seen["access_hash"] = data.access_hash
        return ActionResult(status="ok", action_type="set_main_profile_photo", account_id="acc-1")

    monkeypatch.setattr("services.accounts.set_account_main_profile_photo", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/photo/main",
            json={
                "photo_id": "9007199254740993",  # 2^53 + 1, unrepresentable as a JS number
                "access_hash": "-8000000000000000000",
                "file_reference": "YWJj",
            },
        )
    assert resp.status_code == 200
    assert seen["photo_id"] == 9007199254740993
    assert seen["access_hash"] == -8000000000000000000
