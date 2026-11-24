"""Tombstones: written once per kind and key, cleared on confirmation, never inferred."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import configure_database
from core.repositories.pending_cleanups import (
    add_pending_cleanup,
    clear_pending_cleanup,
    fetch_pending_cleanups,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


@pytest.mark.asyncio
async def test_a_tombstone_is_kept_once_listed_by_kind_and_cleared() -> None:
    await add_pending_cleanup("pod", "telebuba-a", requested_at="2026-11-01")
    await add_pending_cleanup("pod", "telebuba-a", requested_at="2026-11-02")
    await add_pending_cleanup("pod", "telebuba-b", requested_at="2026-11-03")
    await add_pending_cleanup("other", "x", requested_at="2026-11-01")

    assert await fetch_pending_cleanups("pod") == ["telebuba-a", "telebuba-b"]

    await clear_pending_cleanup("pod", "telebuba-a")
    await clear_pending_cleanup("pod", "never-written")

    assert await fetch_pending_cleanups("pod") == ["telebuba-b"]
    assert await fetch_pending_cleanups("other") == ["x"]
