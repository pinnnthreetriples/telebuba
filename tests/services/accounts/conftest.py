"""Shared fixtures for account service tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from services.accounts._import_locks import _IMPORT_LOCKS

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    # Per-key import locks are module-level and bind to the loop alive when first
    # awaited; clear them so each function-scoped test gets fresh locks (mirrors
    # warming's _ACCOUNT_LOCKS reset).
    _IMPORT_LOCKS.clear()
    reset_logging_for_tests()
    setup_logging()
    yield
    _IMPORT_LOCKS.clear()
    reset_logging_for_tests()
