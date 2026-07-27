"""API foundation tests — health, the accounts seed endpoint, and the error envelope."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from schemas.accounts import AccountCreate
from services.accounts import add_account

if TYPE_CHECKING:
    from fastapi import FastAPI


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_health_returns_ok(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_accounts_returns_page_envelope(app: FastAPI) -> None:
    await add_account(AccountCreate(account_id="acc-1", label="One"))
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "next_cursor"}
    assert [a["account_id"] for a in body["items"]] == ["acc-1"]
    assert body["next_cursor"] is None


@pytest.mark.asyncio
async def test_accounts_pagination_emits_next_cursor(app: FastAPI) -> None:
    for i in range(3):
        await add_account(AccountCreate(account_id=f"acc-{i}", label=f"A{i}"))
    async with _client(app) as client:
        first = await client.get("/api/v1/accounts", params={"limit": 2})
        assert first.status_code == 200
        page1 = first.json()
        assert len(page1["items"]) == 2
        assert page1["next_cursor"] == "2"

        second = await client.get(
            "/api/v1/accounts",
            params={"limit": 2, "cursor": page1["next_cursor"]},
        )
    page2 = second.json()
    assert len(page2["items"]) == 1
    assert page2["next_cursor"] is None


@pytest.mark.asyncio
async def test_invalid_cursor_returns_error_envelope(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts", params={"cursor": "not-an-int"})
    assert resp.status_code == 400
    assert resp.json() == {"error": {"code": "bad_request", "message": "invalid pagination cursor"}}


@pytest.mark.asyncio
async def test_validation_error_is_remapped_into_envelope(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts", params={"limit": 0})  # below ge=1
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "validation_error"
    # ``message`` is a CODE, not English prose: the SPA renders it verbatim as the
    # toast fallback, so a hardcoded sentence reached the operator untranslated.
    assert error["message"] == "validation_error"
    # The offending field path is reported so the SPA can attach it to the input.
    assert any("limit" in key for key in error["fields"])


@pytest.mark.asyncio
async def test_unexpected_error_returns_generic_envelope(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(**_kwargs: object) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr("services.accounts.list_accounts_page", _boom)
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts")
    assert resp.status_code == 500
    assert resp.json() == {"error": {"code": "internal_error", "message": "Internal server error"}}


# --------------------------------------------------------------------------- #
# The envelope must also be DECLARED, not just emitted. The runtime tests above
# passed for months while the OpenAPI document described the accounts routes as
# answering FastAPI's ``HTTPValidationError`` with a ``detail`` key — a body
# ``_handle_validation_error`` replaces, so ``detail`` never reached the wire.
# Every error type in the generated TypeScript client was therefore wrong, and
# the CI drift gate could not see a change to the real shape.
# --------------------------------------------------------------------------- #
_DECLARED_ERROR_STATUSES = ("400", "401", "404", "422", "500", "503")


def test_error_envelope_is_declared_in_the_openapi_document(app: FastAPI) -> None:
    schema = app.openapi()
    assert "ErrorEnvelope" in schema["components"]["schemas"]
    responses = schema["paths"]["/api/v1/accounts"]["get"]["responses"]
    for status in _DECLARED_ERROR_STATUSES:
        ref = responses[status]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("/ErrorEnvelope"), f"{status} does not document the envelope"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/accounts/{account_id}/story",
        "/api/v1/accounts/{account_id}/channels",
        "/api/v1/accounts/{account_id}/privacy",
    ],
)
def test_the_envelope_reaches_the_mounted_sub_routers(app: FastAPI, path: str) -> None:
    """Media / channels / privacy are mounted onto the accounts router.

    ``include_router`` merges the including router's ``responses`` into each child
    route, so declaring them once covers the whole account-editing surface. If that
    ever stops holding, the generated client silently loses its error types again.
    """
    operation = next(iter(app.openapi()["paths"][path].values()))
    for status in _DECLARED_ERROR_STATUSES:
        ref = operation["responses"][status]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("/ErrorEnvelope")
    # The auto-generated 422 it replaces must be gone from these operations.
    assert "HTTPValidationError" not in str(operation["responses"]["422"])


def test_only_the_session_creating_routes_declare_a_conflict(app: FastAPI) -> None:
    """409 is real on exactly two routes, so it is not declared router-wide."""
    paths = app.openapi()["paths"]
    assert "409" in paths["/api/v1/accounts/import-session"]["post"]["responses"]
    assert "409" in paths["/api/v1/accounts/start-login"]["post"]["responses"]
    assert "409" not in paths["/api/v1/accounts"]["get"]["responses"]
