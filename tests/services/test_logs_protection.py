"""A "clear logs" never removes recent WARNING/ERROR rows: the journal is the custody audit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import update

from core.config import settings
from core.db import _get_engine, _logs, configure_database
from core.logging import log_event, reset_logging_for_tests, setup_logging
from schemas.logs import LogFilter
from services.logs import clear_logs, count_matching_logs, load_logs_page

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    monkeypatch.setattr(settings.logging, "protected_retention_days", 30)
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


def _age(event: str, days: int) -> None:
    stamp = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with _get_engine().begin() as connection:
        connection.execute(update(_logs).where(_logs.c.event == event).values(created_at=stamp))


async def _events() -> set[str]:
    return {entry.event for entry in (await load_logs_page(LogFilter())).entries}


async def _seed() -> None:
    await log_event("INFO", "info_recent")
    await log_event("WARNING", "warn_recent")
    await log_event("ERROR", "error_recent")
    await log_event("INFO", "info_old")
    await log_event("WARNING", "warn_old")
    await log_event("ERROR", "error_old")
    for name in ("info_old", "warn_old", "error_old"):
        _age(name, 45)


@pytest.mark.asyncio
async def test_a_full_clear_keeps_recent_warnings_and_errors() -> None:
    await _seed()

    result = await clear_logs("")

    assert result.deleted == 4
    assert result.kept_protected == 2
    assert await _events() == {"warn_recent", "error_recent", "logs_cleared"}


@pytest.mark.asyncio
async def test_a_scoped_clear_protects_within_its_prefix_only() -> None:
    await log_event("WARNING", "warming_recent_problem")
    await log_event("INFO", "warming_note")
    await log_event("WARNING", "other_recent_problem")

    result = await clear_logs("warming_")

    assert result.deleted == 1
    assert result.kept_protected == 1  # the unrelated warning is outside the prefix
    assert await _events() == {"warming_recent_problem", "other_recent_problem", "logs_cleared"}


@pytest.mark.asyncio
async def test_the_count_agrees_with_the_clear_and_reports_what_is_kept() -> None:
    await _seed()

    counted = await count_matching_logs("")
    result = await clear_logs("")

    assert (counted.matching, counted.kept_protected) == (4, 2)
    assert (result.deleted, result.kept_protected) == (4, 2)


@pytest.mark.asyncio
async def test_protection_ends_after_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    await log_event("WARNING", "warn_recent")
    _age("warn_recent", 10)
    monkeypatch.setattr(settings.logging, "protected_retention_days", 5)

    result = await clear_logs("")

    assert (result.deleted, result.kept_protected) == (1, 0)


@pytest.mark.asyncio
async def test_the_audit_row_reports_counts_and_a_prefix_flag_not_the_prefix() -> None:
    await _seed()
    long_prefix = ",".join(f"prefix_{index}" for index in range(50)) + ",info_"

    await clear_logs(long_prefix)

    audit = next(
        entry
        for entry in (await load_logs_page(LogFilter())).entries
        if entry.event == "logs_cleared"
    )
    assert audit.level == "INFO"
    assert audit.extra == {"deleted": 2, "prefix_scoped": True, "kept_protected": 0}


@pytest.mark.asyncio
async def test_a_clear_that_removes_nothing_writes_no_audit_row() -> None:
    await log_event("WARNING", "warn_recent")

    result = await clear_logs("")

    assert (result.deleted, result.kept_protected) == (0, 1)
    assert await _events() == {"warn_recent"}
