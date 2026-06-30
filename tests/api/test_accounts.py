"""Accounts endpoint tests — thin routes over mocked services + real delete."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from schemas.accounts import AccountCreate, AccountRead
from schemas.phone_login import PhoneCodeRequestResult
from schemas.profile_media import (
    AccountProfileMusicRemove,
    AccountProfileView,
    ProfileMusicView,
    ProfilePhotoView,
    ProfileStoryView,
)
from schemas.spam_status import SpamStatusVerdict
from schemas.tdata import TdataImportResult
from schemas.telegram_actions import ActionResult
from services.accounts import PhoneLoginError, SessionAlreadyExistsError, add_account

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
async def test_profile_snapshot_returns_view(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str) -> AccountProfileView:  # noqa: ARG001
        return AccountProfileView(
            avatar_data_uri="data:image/jpeg;base64,YWJj",
            photos=[ProfilePhotoView(photo_id=1, access_hash=2, file_reference="YWJj")],
            stories=[ProfileStoryView(story_id=5, kind="image", privacy_preset="contacts")],
            music=[ProfileMusicView(file_id=7, title="T", access_hash=3, file_reference="YWJj")],
        )

    monkeypatch.setattr("services.accounts.account_profile_view", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/acc-1/profile-snapshot")
    assert resp.status_code == 200
    body = resp.json()
    assert body["photos"][0]["photo_id"] == 1
    assert body["music"][0]["title"] == "T"


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
            files={"file": ("s.jpg", b"img", "image/jpeg")},
            data={"media_kind": "image", "privacy_preset": "contacts"},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


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
async def test_remove_music_decodes_file_reference(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    async def _fake(data: AccountProfileMusicRemove) -> ActionResult:
        seen["ref"] = data.file_reference
        return ActionResult(status="ok", action_type="remove_profile_music", account_id="acc-1")

    monkeypatch.setattr("services.accounts.remove_account_profile_music", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/music/remove",
            json={"file_id": 7, "access_hash": 3, "file_reference": "YWJj"},
        )
    assert resp.status_code == 200
    assert seen["ref"] == b"abc"  # base64 "YWJj" -> b"abc"


@pytest.mark.asyncio
async def test_remove_photo_bad_reference_is_400(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/photo/remove",
            json={"photo_id": 1, "access_hash": 2, "file_reference": "!!notbase64!!"},
        )
    assert resp.status_code == 400
