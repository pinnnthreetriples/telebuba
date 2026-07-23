"""Core account endpoint tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from schemas.accounts import AccountCreate, AccountRead
from schemas.phone_login import PhoneCodeRequestResult
from schemas.spam_status import SpamStatusVerdict
from schemas.tdata import TdataImportResult
from services.accounts import (
    AccountActionError,
    PhoneLoginError,
    SessionAlreadyExistsError,
    add_account,
)
from tests.api.accounts_helpers import account as _account
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI


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
async def test_import_session_oversized_rejected_before_read(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An over-cap .session upload is refused from the multipart size, pre-read.

    The service is patched to explode if reached — so the 400 proves the body
    was never buffered into the import flow. Message matches the service check.
    """
    monkeypatch.setattr(settings.profile_media, "session_max_bytes", 1)

    async def _fake(data: object) -> AccountRead:  # noqa: ARG001
        msg = "service must not be reached when the size cap fails"
        raise AssertionError(msg)

    monkeypatch.setattr("services.accounts.import_account_session", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/import-session",
            files={"file": ("acc.session", b"too-big", "application/octet-stream")},
        )
    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "Session file is too large (>1 bytes)"


@pytest.mark.asyncio
async def test_import_tdata_oversized_rejected_before_read(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An over-cap tdata.zip upload is refused pre-read (memory-exhaustion guard)."""
    monkeypatch.setattr(settings.profile_media, "tdata_max_bytes", 1)

    async def _fake(request: object) -> TdataImportResult:  # noqa: ARG001
        msg = "service must not be reached when the size cap fails"
        raise AssertionError(msg)

    monkeypatch.setattr("services.accounts.import_account_tdata", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/import-tdata",
            files={"file": ("tdata.zip", b"zip-bytes", "application/zip")},
        )
    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "tdata archive is too large"


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
