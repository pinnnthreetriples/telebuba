"""The lazy engine build must survive two first-touch calls landing at once.

Every repository call runs in its own ``asyncio.to_thread`` worker, so the very first
two DB calls after ``configure_database`` genuinely race the ``_state.engine is None``
check. Unguarded, both build an engine, the loser is dropped WITHOUT ``dispose()``, and
its pooled sqlite3 connection surfaces much later as ``ResourceWarning: unclosed
database`` — which ``filterwarnings = error`` then charges to whatever test happened to
be running at that garbage collection. Both engines also run ``create_all`` +
``apply_migrations`` against the same file at the same time.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import pytest

import core.db as core_db
from core.db import (
    _get_engine,  # type: ignore[attr-defined]
    configure_database,
)

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine


@pytest.mark.asyncio
async def test_two_first_touch_calls_build_exactly_one_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_database(tmp_path / "telebuba.db")
    built: list[Engine] = []
    real_create_engine = core_db.create_engine

    def _slow_create_engine(*args: Any, **kwargs: Any) -> Engine:
        # Widen the window the two workers race over, so a missing guard fails every
        # run rather than one in a few hundred.
        time.sleep(0.2)
        engine = real_create_engine(*args, **kwargs)
        built.append(engine)
        return engine

    monkeypatch.setattr(core_db, "create_engine", _slow_create_engine)

    first, second = await asyncio.gather(
        asyncio.to_thread(_get_engine),
        asyncio.to_thread(_get_engine),
    )

    assert len(built) == 1
    assert first is second is built[0]
