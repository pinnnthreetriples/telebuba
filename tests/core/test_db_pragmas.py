"""SQLite PRAGMA configuration tests for ``core.db``.

WAL + busy_timeout + synchronous=NORMAL are required so concurrent warming
loops (each running in its own ``asyncio.to_thread``) do not hit
``database is locked`` under load.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from core.config import settings
from core.db import (
    _get_engine,  # type: ignore[attr-defined]
    configure_database,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.gemini, "api_key", "")
    monkeypatch.setattr(settings.gemini, "model", "gemini-2.5-flash")


def test_sqlite_uses_wal_journal() -> None:
    engine = _get_engine()
    with engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar_one()
    assert str(mode).lower() == "wal"


def test_sqlite_busy_timeout_set() -> None:
    engine = _get_engine()
    with engine.connect() as conn:
        timeout_ms = conn.execute(text("PRAGMA busy_timeout")).scalar_one()
    assert int(timeout_ms) >= 5000


def test_sqlite_synchronous_normal() -> None:
    engine = _get_engine()
    with engine.connect() as conn:
        sync = conn.execute(text("PRAGMA synchronous")).scalar_one()
    assert int(sync) == 1
