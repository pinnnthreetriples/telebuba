"""Shared fixtures for the scheduled-post service tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from services import warming

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    # No pacing sleeps: the gaps are proven by the pacing module's own tests.
    monkeypatch.setattr(settings.scheduled_posts, "global_gap_min_seconds", 0.0)
    monkeypatch.setattr(settings.scheduled_posts, "global_gap_max_seconds", 0.0)
    monkeypatch.setattr(settings.scheduled_posts, "account_gap_seconds", 0.0)
    warming._ACCOUNT_LOCKS.clear()
    reset_logging_for_tests()
    setup_logging()
    yield
    warming._ACCOUNT_LOCKS.clear()
    reset_logging_for_tests()
