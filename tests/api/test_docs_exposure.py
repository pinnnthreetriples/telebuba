"""The interactive API docs and the schema are closed unless the operator opts in."""

from __future__ import annotations

import httpx
import pytest

from api import create_app
from core.config import settings

_DOC_PATHS = ("/docs", "/redoc", "/openapi.json")


def _client(*, enabled: bool, monkeypatch: pytest.MonkeyPatch) -> httpx.AsyncClient:
    monkeypatch.setattr(settings.api, "docs_enabled", enabled)
    transport = httpx.ASGITransport(app=create_app(), raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _DOC_PATHS)
async def test_docs_are_not_served_by_default(path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert settings.api.docs_enabled is False
    async with _client(enabled=False, monkeypatch=monkeypatch) as client:
        resp = await client.get(path)
    assert resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _DOC_PATHS)
async def test_docs_are_served_when_enabled(path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _client(enabled=True, monkeypatch=monkeypatch) as client:
        resp = await client.get(path)
    assert resp.status_code == 200


def test_the_schema_is_still_available_in_process_for_the_client_generator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``tools/gen_api.py`` calls ``create_app().openapi()``, not the HTTP route."""
    monkeypatch.setattr(settings.api, "docs_enabled", False)
    assert "/api/v1/health" in create_app().openapi()["paths"]
