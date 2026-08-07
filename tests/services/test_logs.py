"""Tests for the logs service layer."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import log_event, reset_logging_for_tests, setup_logging
from schemas.logs import LogFilter
from services.logs import clear_logs, count_matching_logs, load_logs_page

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
async def test_problems_only_returns_warnings_and_errors() -> None:
    await _seed_sample_events()

    state = await load_logs_page(LogFilter(problems_only=True))

    assert state.summary.total == 2
    assert state.summary.warning == 1
    assert state.summary.error == 1
    assert state.summary.success == 0
    # newest-first: the error (acc-2) was seeded after the warning (acc-1)
    assert state.entries[0].status == "error"
    assert state.entries[0].account_id == "acc-2"
    assert state.entries[1].status == "warning"


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


@pytest.mark.asyncio
async def test_event_prefix_keeps_only_matching_events() -> None:
    await log_event("INFO", "neurocomment_posted", account_id="acc-1")
    await log_event("INFO", "warming_cycle_completed", account_id="acc-1")
    await log_event("WARNING", "neurocomment_post_failed", account_id="acc-2")

    state = await load_logs_page(LogFilter(event_prefix="neurocomment"))

    assert {e.event for e in state.entries} == {"neurocomment_posted", "neurocomment_post_failed"}
    assert state.summary.total == 2


@pytest.mark.asyncio
async def test_event_prefix_accepts_several_comma_separated_prefixes() -> None:
    """A multi-prefix filter must return every named prefix and nothing else.

    The prefixes here are illustrative — the warming terminal now asks for
    ``warming_,spam_status``; this exercises the comma-splitting, not that view.
    """
    await log_event("INFO", "warming_cycle_completed", account_id="acc-1")
    await log_event("WARNING", "telegram_action_unavailable", account_id="acc-1")
    await log_event("INFO", "neurocomment_posted", account_id="acc-1")

    state = await load_logs_page(LogFilter(event_prefix="warming_,telegram_"))

    assert {e.event for e in state.entries} == {
        "warming_cycle_completed",
        "telegram_action_unavailable",
    }


@pytest.mark.asyncio
async def test_event_prefix_ignores_a_blank_part_among_valid_ones() -> None:
    """A trailing comma must not widen the filter to everything."""
    await log_event("INFO", "warming_cycle_completed")
    await log_event("INFO", "neurocomment_posted")

    state = await load_logs_page(LogFilter(event_prefix="warming_,"))

    assert {e.event for e in state.entries} == {"warming_cycle_completed"}


@pytest.mark.asyncio
async def test_clear_logs_by_prefix_list_deletes_exactly_the_union() -> None:
    """Only the named prefixes go — plus the purge's own audit row arrives."""
    await log_event("INFO", "warming_cycle_completed")
    await log_event("WARNING", "telegram_action_unavailable")
    await log_event("INFO", "spam_status_refreshed")
    await log_event("INFO", "neurocomment_posted")

    result = await clear_logs("warming_,telegram_")

    assert result.deleted == 2
    remaining = await load_logs_page(LogFilter())
    assert {e.event for e in remaining.entries} == {
        "spam_status_refreshed",
        "neurocomment_posted",
        "logs_cleared",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("wildcard", ["_", "%"])
async def test_event_prefix_wildcards_are_literal_not_patterns(wildcard: str) -> None:
    """``_`` / ``%`` must be escaped: unescaped they matched (and purged) every row."""
    await log_event("INFO", "warming_cycle_completed")
    await log_event("INFO", "neurocomment_posted")

    state = await load_logs_page(LogFilter(event_prefix=wildcard))
    assert state.entries == []

    result = await clear_logs(wildcard)
    assert result.deleted == 0
    assert (await load_logs_page(LogFilter())).summary.total == 2


@pytest.mark.asyncio
async def test_event_prefix_matches_a_literal_backslash() -> None:
    r"""A backslash in the prefix must survive as data, not be eaten as the escape char.

    Doubling it is what makes that work: ``LIKE 'warm\path%' ESCAPE '\'`` reads ``\p``
    as an escaped ``p`` and so matches ``warmpath_x`` instead — the decoy row below
    catches exactly that, in both directions.
    """
    await log_event("INFO", "warm\\path_x")
    await log_event("INFO", "warmpath_x")

    state = await load_logs_page(LogFilter(event_prefix="warm\\path"))

    assert {e.event for e in state.entries} == {"warm\\path_x"}


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", [" ", ",", ",,", " , "])
async def test_all_blank_event_prefix_matches_nothing_and_purges_nothing(blank: str) -> None:
    """A non-empty prefix whose parts are all blank is a filter, NOT "no filter".

    Collapsing it to "no filter" would make ``DELETE /api/v1/logs?event_prefix=,``
    wipe the whole table — the same class of bug as the unescaped-wildcard one.
    """
    await log_event("INFO", "warming_cycle_completed")
    await log_event("INFO", "neurocomment_posted")

    state = await load_logs_page(LogFilter(event_prefix=blank))
    assert state.entries == []

    result = await clear_logs(blank)
    assert result.deleted == 0
    assert (await load_logs_page(LogFilter())).summary.total == 2


@pytest.mark.asyncio
async def test_event_prefix_empty_is_no_filter() -> None:
    await log_event("INFO", "neurocomment_posted")
    await log_event("INFO", "warming_cycle_completed")

    state = await load_logs_page(LogFilter(event_prefix=""))

    assert state.summary.total == 2


@pytest.mark.asyncio
async def test_clear_logs_by_prefix_removes_only_matching_rows() -> None:
    """Rows outside the prefix survive; the audit row of the purge joins them."""
    await log_event("INFO", "neurocomment_posted")
    await log_event("WARNING", "neurocomment_post_failed")
    await log_event("INFO", "warming_cycle_completed")

    result = await clear_logs("neurocomment")

    assert result.deleted == 2
    remaining = await load_logs_page(LogFilter())
    assert {e.event for e in remaining.entries} == {"warming_cycle_completed", "logs_cleared"}


@pytest.mark.asyncio
async def test_clear_logs_empty_prefix_wipes_everything() -> None:
    """An empty prefix takes every existing row — and then the purge records itself.

    So "everything deleted" is no longer "table empty": the audit row is written after
    the delete, precisely so it outlives it. Asserting emptiness here would be asserting
    that the wipe left no trace, which is the bug this row exists to close. The empty
    prefix reaches that row as ``*``, legible to the operator reading it.
    """
    await log_event("INFO", "neurocomment_posted")
    await log_event("INFO", "warming_cycle_completed")

    result = await clear_logs("")

    assert result.deleted == 2
    remaining = (await load_logs_page(LogFilter())).entries
    assert [e.event for e in remaining] == ["logs_cleared"]
    assert remaining[0].extra == {"deleted": 2, "event_prefix": "*"}


@pytest.mark.asyncio
async def test_clear_logs_writes_an_audit_row_that_its_own_prefix_cannot_delete() -> None:
    """A purge must leave a record of itself under a code the same purge cannot reach.

    One press of the neurocomment "clear logs" button erased a month of history and
    recorded nothing, so the silence read as a broken system for days. Two properties
    keep the record: it is written AFTER the delete, and its code carries no
    ``neurocomment`` prefix — otherwise the next press would erase the evidence of the
    previous one.
    """
    await log_event("INFO", "neurocomment_posted")
    await log_event("WARNING", "neurocomment_post_failed")
    await log_event("INFO", "warming_cycle_completed")

    await clear_logs("neurocomment")

    entries = (await load_logs_page(LogFilter())).entries
    audit = next(entry for entry in entries if entry.event == "logs_cleared")
    assert audit.level == "INFO"
    assert audit.extra == {"deleted": 2, "event_prefix": "neurocomment"}

    second = await clear_logs("neurocomment")

    assert second.deleted == 0
    assert "logs_cleared" in {e.event for e in (await load_logs_page(LogFilter())).entries}


@pytest.mark.asyncio
async def test_clear_logs_writes_no_audit_row_when_nothing_was_deleted() -> None:
    """A press that deleted nothing leaves nothing behind, like the retention sweeps.

    Otherwise an operator poking an already-empty feed fills it with clear events.
    """
    await log_event("INFO", "warming_cycle_completed")

    result = await clear_logs("neurocomment")

    assert result.deleted == 0
    assert {e.event for e in (await load_logs_page(LogFilter())).entries} == {
        "warming_cycle_completed",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_prefix", "expected"),
    [("", 5), ("warming_,telegram_", 2), (" ", 0), (",", 0), ("_", 1), ("%", 1)],
)
async def test_count_matching_logs_agrees_with_what_clear_logs_then_deletes(
    event_prefix: str,
    expected: int,
) -> None:
    """The number the operator confirms and the number that goes must be one clause.

    Every shape ``_event_prefix_clause`` treats specially is here: "no filter" (empty),
    a comma-separated union, an all-blank value that is a filter matching nothing, and a
    prefix of bare SQL wildcards — escaped, so ``_``/``%`` match the two rows literally
    named that way and not, as they once did, every row in the table.
    """
    await log_event("INFO", "warming_cycle_completed")
    await log_event("WARNING", "telegram_action_unavailable")
    await log_event("INFO", "neurocomment_posted")
    await log_event("INFO", "_underscore_prefixed")
    await log_event("INFO", "%percent_prefixed")

    counted = await count_matching_logs(event_prefix)

    assert counted.matching == expected
    assert (await clear_logs(event_prefix)).deleted == counted.matching
