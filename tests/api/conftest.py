"""Shared fixtures for the API tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from api import create_app
from api.deps import get_current_user
from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from schemas.auth import UserRead

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fastapi import FastAPI


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


@pytest.fixture
def app() -> FastAPI:
    # No lifespan in tests: the warming/neurocomment runtimes must not start.
    # Feature tests run authenticated — the auth gate itself is exercised with a
    # raw create_app() in tests/api/test_auth.py.
    application = create_app()
    application.dependency_overrides[get_current_user] = lambda: UserRead(
        id="test-admin",
        username="admin",
        role="admin",
    )
    return application
