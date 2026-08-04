"""Liveness vs readiness.

``/health`` answers "this process is up" and touches no I/O — which is exactly why
it cannot decide whether to send traffic here: it stays green while the sole
datastore is missing, locked, or corrupt. ``/ready`` is the probe that looks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from api import create_app
from core.db import check_database_reachable

if TYPE_CHECKING:
    from fastapi import FastAPI

_DB_PATH_MARKER = "telebuba.db"


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_ready_reports_ok_when_the_database_answers(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.get("/api/v1/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": True}


@pytest.mark.asyncio
async def test_ready_is_503_when_the_database_is_unreachable(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The status CODE has to carry it — a 200 saying "unavailable" no probe reads."""

    async def _boom() -> None:
        msg = "unable to open database file"
        raise OSError(msg)

    monkeypatch.setattr("services.health.check_database_reachable", _boom)
    async with _client(app) as client:
        resp = await client.get("/api/v1/ready")
    assert resp.status_code == 503
    assert resp.json() == {"status": "unavailable", "database": False}


@pytest.mark.asyncio
async def test_the_unavailable_body_leaks_nothing_about_the_failure(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No exception text, no path, no SQL.

    SQLAlchemy's ``StatementError.__str__`` appends the failing statement and its
    bound parameters, and for this datastore that includes its filesystem path. The
    probe is unauthenticated, so the body must stay two booleans wide.
    """

    async def _boom() -> None:
        msg = (
            "(sqlite3.OperationalError) unable to open database file "
            f"[SQL: SELECT 1] /srv/{_DB_PATH_MARKER}"
        )
        raise RuntimeError(msg)

    monkeypatch.setattr("services.health.check_database_reachable", _boom)
    async with _client(app) as client:
        resp = await client.get("/api/v1/ready")
    body = resp.text
    assert resp.status_code == 503
    assert set(resp.json()) == {"status", "database"}
    for leak in (_DB_PATH_MARKER, "SELECT 1", "sqlite3", "OperationalError", "/srv"):
        assert leak not in body


@pytest.mark.asyncio
async def test_readiness_needs_no_session() -> None:
    """A supervisor holds no cookie, so the probe must answer the raw app."""
    async with _client(create_app()) as client:
        resp = await client.get("/api/v1/ready")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_liveness_still_answers_without_touching_the_database(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The distinction IS the feature: liveness must not depend on the datastore."""

    async def _boom() -> None:
        msg = "unable to open database file"
        raise OSError(msg)

    monkeypatch.setattr("services.health.check_database_reachable", _boom)
    async with _client(app) as client:
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_check_database_reachable_really_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not an ``engine is not None`` test — a broken file has to be caught."""

    def _broken() -> None:
        msg = "database disk image is malformed"
        raise RuntimeError(msg)

    monkeypatch.setattr("core.db._get_engine", _broken)
    with pytest.raises(RuntimeError, match="malformed"):
        await check_database_reachable()
