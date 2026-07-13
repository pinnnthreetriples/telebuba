"""Accounts endpoint tests — thin routes over mocked services + real delete."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from core.config import settings
from schemas.accounts import AccountCreate, AccountRead
from schemas.phone_login import PhoneCodeRequestResult
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
from schemas.spam_status import SpamStatusVerdict
from schemas.tdata import TdataImportResult
from schemas.telegram_actions import ActionResult
from services.accounts import (
    AccountActionError,
    PhoneLoginError,
    SessionAlreadyExistsError,
    add_account,
)

if TYPE_CHECKING:
    from fastapi import FastAPI


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _account(account_id: str = "acc-1") -> AccountRead:
    return AccountRead(account_id=account_id, status="alive", created_at="now", updated_at="now")


@pytest.mark.asyncio
async def test_check_account_returns_the_checked_account(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(body: object) -> AccountRead:  # noqa: ARG001
        return _account("acc-1")

    monkeypatch.setattr("services.accounts.check_account_session", _fake)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/check", json={"account_id": "acc-1"})
    assert resp.status_code == 200
    assert resp.json()["account_id"] == "acc-1"


@pytest.mark.asyncio
async def test_check_account_maps_value_error_to_400(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(body: object) -> AccountRead:  # noqa: ARG001
        msg = "no session"
        raise ValueError(msg)

    monkeypatch.setattr("services.accounts.check_account_session", _boom)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/check", json={"account_id": "acc-1"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_spam_check_returns_the_fresh_verdict(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str, *, force: bool) -> SpamStatusVerdict:
        assert force is True
        return SpamStatusVerdict(
            account_id=account_id,
            status="limited",
            detail="until 2026-07-01",
            checked_at="2026-06-30T00:00:00+00:00",
        )

    monkeypatch.setattr("services.spam_status.refresh_spam_status", _fake)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/spam-check")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "limited"
    assert body["detail"] == "until 2026-07-01"


@pytest.mark.asyncio
async def test_request_code_returns_confirmation(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str) -> PhoneCodeRequestResult:
        return PhoneCodeRequestResult(account_id=account_id, phone="79990001122")

    monkeypatch.setattr("services.accounts.request_login_code", _fake)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/request-code")
    assert resp.status_code == 200
    assert resp.json()["phone"] == "79990001122"


@pytest.mark.asyncio
async def test_request_code_maps_login_error_to_400(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(account_id: str) -> PhoneCodeRequestResult:  # noqa: ARG001
        msg = "No phone number on record"
        raise PhoneLoginError(msg)

    monkeypatch.setattr("services.accounts.request_login_code", _boom)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/request-code")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_submit_code_returns_the_account(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str, code: str, password: str | None) -> AccountRead:  # noqa: ARG001
        return _account(account_id)

    monkeypatch.setattr("services.accounts.submit_login_code", _fake)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/submit-code", json={"code": "12345"})
    assert resp.status_code == 200
    assert resp.json()["account_id"] == "acc-1"


@pytest.mark.asyncio
async def test_submit_code_maps_login_error_to_400(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(account_id: str, code: str, password: str | None) -> AccountRead:  # noqa: ARG001
        msg = "Sign-in failed"
        raise PhoneLoginError(msg)

    monkeypatch.setattr("services.accounts.submit_login_code", _boom)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/submit-code", json={"code": "00000"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_logout_and_reset_return_the_account(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str) -> AccountRead:
        return _account(account_id)

    monkeypatch.setattr("services.accounts.logout_account", _fake)
    monkeypatch.setattr("services.accounts.reset_account_session", _fake)
    async with _client(app) as client:
        logout = await client.post("/api/v1/accounts/acc-1/logout")
        reset = await client.post("/api/v1/accounts/acc-1/reset-session")
    assert logout.status_code == 200
    assert reset.status_code == 200
    assert reset.json()["account_id"] == "acc-1"


@pytest.mark.asyncio
async def test_update_profile_returns_the_account(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(body: object) -> AccountRead:  # noqa: ARG001
        return _account("acc-1")

    monkeypatch.setattr("services.accounts.update_account_profile", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/profile",
            json={"account_id": "acc-1", "first_name": "New"},
        )
    assert resp.status_code == 200
    assert resp.json()["account_id"] == "acc-1"


@pytest.mark.asyncio
async def test_update_profile_maps_value_error_to_400(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(body: object) -> AccountRead:  # noqa: ARG001
        msg = "bad profile"
        raise ValueError(msg)

    monkeypatch.setattr("services.accounts.update_account_profile", _boom)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/profile",
            json={"account_id": "acc-1", "first_name": "New"},
        )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_update_profile_flood_wait_surfaces_retry_seconds(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flood-limited profile update keeps the wait duration on the wire.

    ``message`` stays the stable code and the server-mandated seconds land in
    the envelope's ``fields`` — previously the duration was dropped by the
    ``str(exc)`` collapse and the client had no idea how long to back off.
    """

    async def _flood(body: object) -> AccountRead:  # noqa: ARG001
        code = "flood_wait"
        raise AccountActionError(code, retry_after_seconds=345)

    monkeypatch.setattr("services.accounts.update_account_profile", _flood)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/profile",
            json={"account_id": "acc-1", "first_name": "New"},
        )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "bad_request"
    assert body["error"]["message"] == "flood_wait"
    assert body["error"]["fields"] == {"retry_after_seconds": "345"}


@pytest.mark.asyncio
async def test_update_profile_over_limit_first_name_is_422_with_fields(
    app: FastAPI,
) -> None:
    """Server-side length limits hold without the SPA: 65-char first_name → 422."""
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/profile",
            json={"account_id": "acc-1", "first_name": "x" * 65},
        )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "validation_error"
    assert "body.first_name" in body["error"]["fields"]


@pytest.mark.asyncio
async def test_import_tdata_maps_value_error_to_400(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(request: object) -> TdataImportResult:  # noqa: ARG001
        msg = "not a tdata archive"
        raise ValueError(msg)

    monkeypatch.setattr("services.accounts.import_account_tdata", _boom)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/import-tdata",
            files={"file": ("bad.zip", b"x", "application/zip")},
        )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_delete_account_removes_it(app: FastAPI) -> None:
    await add_account(AccountCreate(account_id="gone", label="Gone"))
    async with _client(app) as client:
        deleted = await client.delete("/api/v1/accounts/gone")
        assert deleted.status_code == 204
        listed = await client.get("/api/v1/accounts")
    assert [a["account_id"] for a in listed.json()["items"]] == []


@pytest.mark.asyncio
async def test_account_stats_endpoint_returns_fleet_counts(app: FastAPI) -> None:
    """GET /accounts/stats serves fleet-wide tile counts (all "new" here)."""
    for i in range(3):
        await add_account(AccountCreate(account_id=f"acc-{i}", label=f"acc-{i}"))
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    # Fresh accounts default to status "new" → the needs_code bucket.
    assert body["needs_code"] == 3
    assert body["active"] == 0
    assert body["idle"] == 0
    assert body["problem"] == 0


@pytest.mark.asyncio
async def test_import_tdata_accepts_multipart(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(request: object) -> TdataImportResult:  # noqa: ARG001
        return TdataImportResult(accounts=[_account("imported")])

    monkeypatch.setattr("services.accounts.import_account_tdata", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/import-tdata",
            files={"file": ("tdata.zip", b"zip-bytes", "application/zip")},
            data={"label": "Batch"},
        )
    assert resp.status_code == 200
    assert [a["account_id"] for a in resp.json()["accounts"]] == ["imported"]


@pytest.mark.asyncio
async def test_import_session_accepts_multipart(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(data: object) -> AccountRead:  # noqa: ARG001
        return _account("from-session")

    monkeypatch.setattr("services.accounts.import_account_session", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/import-session",
            files={"file": ("acc.session", b"session-bytes", "application/octet-stream")},
            data={"label": "S"},
        )
    assert resp.status_code == 200
    assert resp.json()["account_id"] == "from-session"


@pytest.mark.asyncio
async def test_import_session_duplicate_is_409(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(data: object) -> AccountRead:  # noqa: ARG001
        msg = "already exists"
        raise SessionAlreadyExistsError(msg)

    monkeypatch.setattr("services.accounts.import_account_session", _boom)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/import-session",
            files={"file": ("acc.session", b"x", "application/octet-stream")},
        )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_start_login_returns_the_account(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(phone: str, label: str | None = None) -> AccountRead:  # noqa: ARG001
        return _account("79990001122")

    monkeypatch.setattr("services.accounts.start_phone_login", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/start-login",
            json={"phone": "+79990001122"},
        )
    assert resp.status_code == 200
    assert resp.json()["account_id"] == "79990001122"


@pytest.mark.asyncio
async def test_start_login_duplicate_is_409(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(phone: str, label: str | None = None) -> AccountRead:  # noqa: ARG001
        msg = "already exists"
        raise SessionAlreadyExistsError(msg)

    monkeypatch.setattr("services.accounts.start_phone_login", _boom)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/start-login",
            json={"phone": "+79990001122"},
        )
    assert resp.status_code == 409


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
