"""SPA catch-all path-traversal containment (finding #1).

The route helper ``_safe_spa_file`` decides whether a requested path resolves
to a real file *inside* ``frontend/dist``. Starlette's TestClient normalizes
``..`` before the app sees it, so the containment decision is unit-tested on
the pure helper directly with traversal inputs.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
import pytest
from fastapi import FastAPI

import main

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "path",
    [
        "../../.env",  # parent traversal
        "..\\..\\.env",  # backslash traversal (Windows separators)
        "/etc/passwd",  # absolute (posix)
        "C:\\Windows\\win.ini",  # absolute (windows)
        "assets/../../telebuba.db",  # dotdot segment mid-path
    ],
)
def test_safe_spa_file_rejects_traversal_and_absolute_paths(path: str) -> None:
    assert main._safe_spa_file(path) is None


def test_safe_spa_file_resolves_a_normal_asset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    asset = dist / "assets" / "x.js"
    asset.write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(main, "_FRONTEND_DIST", dist)
    resolved = main._safe_spa_file("assets/x.js")
    assert resolved == asset.resolve()


def test_safe_spa_file_returns_none_for_missing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "_FRONTEND_DIST", tmp_path / "dist")
    assert main._safe_spa_file("assets/nope.js") is None


def test_the_shell_revalidates_and_hashed_assets_do_not(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one file whose name never changes must not be kept; the hashed ones must.

    Without this the browser falls back to heuristic freshness for ``index.html``,
    keeps answering from its own copy after a deploy, and loads the PREVIOUS bundle
    by name — the old application against the new backend, with nothing on screen
    to say so. Observed: a rebuilt SPA stayed old until a manual hard reload.
    """
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.ico").write_text("x", encoding="utf-8")
    monkeypatch.setattr(main, "_FRONTEND_DIST", dist)

    app = FastAPI()
    main._mount_frontend(app)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async def cache_for(path: str) -> str:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return (await client.get(path)).headers["cache-control"]

    assert asyncio.run(cache_for("/")) == main._SHELL_CACHE
    # A client-side route is the same shell, so it carries the same promise.
    assert asyncio.run(cache_for("/neuroshilling")) == main._SHELL_CACHE
    # Hashed: a changed file is a different URL, so keeping it forever is safe —
    # and is the reason the hash is in the name at all.
    assert asyncio.run(cache_for("/assets/index-abc123.js")) == main._ASSET_CACHE
    # Unhashed and outside `assets/`: it would outlive its own replacement.
    assert asyncio.run(cache_for("/favicon.ico")) == main._SHELL_CACHE
