"""Account-privacy endpoint tests — thin routes over mocked privacy services."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from api import create_app
from schemas.privacy import (
    AccountPrivacyOutcome,
    AccountPrivacyUpdateRequest,
    AccountPrivacyView,
    BulkPrivacyResult,
)
from schemas.telegram_actions_privacy import PrivacySettingsResult
from services.accounts import AccountNotFoundError
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI

_PRIVACY_URL = "/api/v1/accounts/acc-1/privacy"


@pytest.mark.asyncio
async def test_get_account_privacy_returns_the_three_levels(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str) -> AccountPrivacyView:
        assert account_id == "acc-1"
        return AccountPrivacyView(
            settings=PrivacySettingsResult(
                profile_photo="contacts",
                bio="nobody",
                last_seen="everybody",
            ),
        )

    monkeypatch.setattr("services.accounts.read_account_privacy", _fake)
    async with _client(app) as client:
        resp = await client.get(_PRIVACY_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert body["settings"] == {
        "profile_photo": "contacts",
        "bio": "nobody",
        "last_seen": "everybody",
    }
    assert body["error"] is None


@pytest.mark.asyncio
async def test_get_account_privacy_surfaces_a_refused_read_as_the_error_envelope(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str) -> AccountPrivacyView:  # noqa: ARG001
        return AccountPrivacyView(error="FloodWait(30s)")

    monkeypatch.setattr("services.accounts.read_account_privacy", _fake)
    async with _client(app) as client:
        resp = await client.get(_PRIVACY_URL)

    assert resp.status_code == 200
    assert resp.json() == {"settings": None, "error": "FloodWait(30s)"}


@pytest.mark.asyncio
async def test_get_account_privacy_unknown_account_is_404(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _missing(account_id: str) -> AccountPrivacyView:
        raise AccountNotFoundError(account_id)

    monkeypatch.setattr("services.accounts.read_account_privacy", _missing)
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/nope/privacy")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_set_account_privacy_applies_and_returns_the_fresh_state(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[AccountPrivacyUpdateRequest] = []

    async def _fake(account_id: str, body: AccountPrivacyUpdateRequest) -> AccountPrivacyView:
        assert account_id == "acc-1"
        seen.append(body)
        return AccountPrivacyView(settings=PrivacySettingsResult(profile_photo="everybody"))

    monkeypatch.setattr("services.accounts.apply_account_privacy", _fake)
    async with _client(app) as client:
        resp = await client.put(_PRIVACY_URL, json={"profile_photo": "everybody"})

    assert resp.status_code == 200
    assert resp.json()["settings"]["profile_photo"] == "everybody"
    # ``None`` = unchanged travels through untouched.
    assert [(b.profile_photo, b.bio, b.last_seen) for b in seen] == [("everybody", None, None)]


@pytest.mark.asyncio
async def test_set_account_privacy_unknown_account_is_404(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _missing(account_id: str, body: object) -> AccountPrivacyView:  # noqa: ARG001
        raise AccountNotFoundError(account_id)

    monkeypatch.setattr("services.accounts.apply_account_privacy", _missing)
    async with _client(app) as client:
        resp = await client.put("/api/v1/accounts/nope/privacy", json={"bio": "everybody"})

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_set_account_privacy_rejects_an_unknown_field(app: FastAPI) -> None:
    """``extra="forbid"``: a typo'd key must 422, never silently no-op."""
    async with _client(app) as client:
        resp = await client.put(_PRIVACY_URL, json={"profile_photos": "everybody"})

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_set_account_privacy_rejects_an_all_none_body(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.put(_PRIVACY_URL, json={})

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_set_account_privacy_rejects_an_unknown_level(app: FastAPI) -> None:
    """``unknown`` is a read-only outcome — it is not a settable target."""
    async with _client(app) as client:
        resp = await client.put(_PRIVACY_URL, json={"bio": "unknown"})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_set_all_accounts_privacy_returns_the_bulk_roll_up(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(body: AccountPrivacyUpdateRequest) -> BulkPrivacyResult:
        assert body.last_seen == "nobody"
        return BulkPrivacyResult(
            outcomes=[
                AccountPrivacyOutcome(account_id="acc-1", status="ok"),
                AccountPrivacyOutcome(account_id="acc-2", status="failed", error="flood_wait"),
                AccountPrivacyOutcome(account_id="acc-3", status="skipped"),
            ],
            ok=1,
            failed=1,
            skipped=1,
        )

    monkeypatch.setattr("services.accounts.apply_privacy_to_all_accounts", _fake)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/privacy/all", json={"last_seen": "nobody"})

    assert resp.status_code == 200
    body = resp.json()
    assert (body["ok"], body["failed"], body["skipped"]) == (1, 1, 1)
    assert [o["account_id"] for o in body["outcomes"]] == ["acc-1", "acc-2", "acc-3"]
    assert body["outcomes"][1]["error"] == "flood_wait"


@pytest.mark.asyncio
async def test_set_all_accounts_privacy_rejects_an_all_none_body(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/privacy/all", json={})

    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "url", "json_body"),
    [
        ("GET", _PRIVACY_URL, None),
        ("PUT", _PRIVACY_URL, {"bio": "everybody"}),
        ("POST", "/api/v1/accounts/privacy/all", {"bio": "everybody"}),
    ],
)
async def test_privacy_routes_require_authentication(
    method: str,
    url: str,
    json_body: dict[str, str] | None,
) -> None:
    """Raw app (no ``get_current_user`` override) — the auth gate is real here."""
    async with _client(create_app()) as client:
        resp = await client.request(method, url, json=json_body)

    assert resp.status_code == 401
