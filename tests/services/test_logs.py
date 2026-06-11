"""Tests for the logs service layer."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import log_event, reset_logging_for_tests, setup_logging
from schemas.logs import LogFilter
from services.logs import load_logs_page

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


async def _seed_sample_events() -> None:
    await log_event("INFO", "account_added", account_id="acc-1")
    await log_event("WARNING", "flood_wait", account_id="acc-1", extra={"seconds": 30})
    await log_event("ERROR", "banned", account_id="acc-2")
    await log_event("INFO", "account_added", account_id="acc-3")


@pytest.mark.asyncio
async def test_load_logs_page_returns_all_entries_newest_first() -> None:
    await _seed_sample_events()

    state = await load_logs_page(LogFilter())

    assert state.summary.total == 4
    assert state.summary.success == 2
    assert state.summary.warning == 1
    assert state.summary.error == 1
    # newest-first: last seeded event has the highest id
    assert state.entries[0].event == "account_added"
    assert state.entries[0].account_id == "acc-3"


@pytest.mark.asyncio
async def test_status_filter_limits_to_one_class() -> None:
    await _seed_sample_events()

    state = await load_logs_page(LogFilter(status="warning"))

    assert state.summary.total == 1
    assert state.summary.warning == 1
    assert state.entries[0].status == "warning"
    assert state.entries[0].account_id == "acc-1"


@pytest.mark.asyncio
async def test_account_filter_limits_to_one_account() -> None:
    await _seed_sample_events()

    state = await load_logs_page(LogFilter(account_id="acc-1"))

    assert state.summary.total == 2
    assert all(entry.account_id == "acc-1" for entry in state.entries)


@pytest.mark.asyncio
async def test_combined_filter_intersects_status_and_account() -> None:
    await _seed_sample_events()

    state = await load_logs_page(LogFilter(status="success", account_id="acc-1"))

    assert state.summary.total == 1
    assert state.entries[0].status == "success"
    assert state.entries[0].account_id == "acc-1"


@pytest.mark.asyncio
async def test_empty_table_returns_zero_summary() -> None:
    state = await load_logs_page(LogFilter())

    assert state.entries == []
    assert state.summary.total == 0
    assert state.summary.success == 0
    assert state.summary.warning == 0
    assert state.summary.error == 0


@pytest.mark.asyncio
async def test_limit_caps_returned_rows() -> None:
    for index in range(5):
        await log_event("INFO", "ping", account_id=f"acc-{index}")

    state = await load_logs_page(LogFilter(limit=2))

    assert state.summary.total == 2
    assert len(state.entries) == 2
    # newest-first
    assert state.entries[0].account_id == "acc-4"
    assert state.entries[1].account_id == "acc-3"
