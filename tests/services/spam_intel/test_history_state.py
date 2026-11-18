"""The probe's fields and the history sweep's mark are written independently (one row, two owners).

An operator probe that started before a sweep read the dialog used to write its whole
``ProbeState`` back from the snapshot it took earlier, setting ``history_read_at`` to ``None``
again; the next sweep then read the same dialog a second time.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.repositories.spam_probe_ledger import (
    fetch_probe_state,
    mark_history_read,
    save_probe_outcome,
)
from schemas.spam_intel import ProbeState
from schemas.spam_status import SpamBotHistory
from services import spam_intel
from services.spam_intel import _history

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.services.spam_intel.conftest import FakeProbe

    MakeAccount = Callable[..., Awaitable[None]]


class _Reader:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, account_id: str, _limit: int) -> SpamBotHistory:
        self.calls.append(account_id)
        return SpamBotHistory(account_id=account_id, texts=["an older reply"])


@pytest.fixture(autouse=True)
def reader(monkeypatch: pytest.MonkeyPatch) -> _Reader:
    fake = _Reader()
    monkeypatch.setattr(_history, "read_spambot_history", fake)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(_history, "_sleep", _no_sleep)
    return fake


@pytest.mark.asyncio
async def test_a_probe_already_started_does_not_erase_the_history_mark(
    make_account: MakeAccount, probe: FakeProbe, reader: _Reader
) -> None:
    await make_account("acc-1")
    probe.delay = 0.1
    operator = asyncio.create_task(spam_intel.operator_check("acc-1", confirm_out_of_turn=True))
    await asyncio.sleep(0.03)  # the probe has read its snapshot and is waiting on the bot
    assert await _history.run_history_sweep() == 1
    marked = await fetch_probe_state("acc-1")
    assert marked is not None
    assert marked.history_read_at is not None
    await operator
    state = await fetch_probe_state("acc-1")
    assert state is not None
    assert state.history_read_at == marked.history_read_at
    assert state.last_outcome == "clean"
    # The next sweep has nothing left to read.
    assert await _history.run_history_sweep() == 0
    assert reader.calls == ["acc-1"]


@pytest.mark.asyncio
async def test_the_probe_state_is_folded_from_the_row_as_it_is_when_written() -> None:
    """A failure streak is extended from the stored streak, not from a snapshot taken earlier."""
    await save_probe_outcome(ProbeState(account_id="a1", fail_streak=2, last_error="TimeoutError"))
    await mark_history_read("a1", "2026-11-16T12:00:00+00:00")
    await save_probe_outcome(ProbeState(account_id="a1", fail_streak=3, last_outcome="error"))
    state = await fetch_probe_state("a1")
    assert state is not None
    assert (state.fail_streak, state.history_read_at) == (3, "2026-11-16T12:00:00+00:00")


@pytest.mark.asyncio
async def test_marking_a_history_read_leaves_the_probe_fields_alone() -> None:
    await save_probe_outcome(
        ProbeState(
            account_id="a1",
            fail_streak=2,
            next_allowed_at="2026-11-17T00:00:00+00:00",
            last_caller="warming.cycle",
            last_outcome="error",
            last_error="TimeoutError",
        )
    )
    await mark_history_read("a1", "2026-11-16T12:00:00+00:00")
    state = await fetch_probe_state("a1")
    assert state is not None
    assert (state.fail_streak, state.last_error, state.last_caller) == (
        2,
        "TimeoutError",
        "warming.cycle",
    )
    assert state.next_allowed_at == "2026-11-17T00:00:00+00:00"
    assert state.history_read_at == "2026-11-16T12:00:00+00:00"


@pytest.mark.asyncio
async def test_a_history_mark_creates_the_row_when_there_is_no_probe_yet() -> None:
    await mark_history_read("a1", "2026-11-16T12:00:00+00:00")
    state = await fetch_probe_state("a1")
    assert state is not None
    assert (state.fail_streak, state.last_outcome, state.history_read_at) == (
        0,
        None,
        "2026-11-16T12:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_a_read_account_is_refused_at_the_gate_where_the_row_would_be_reserved(
    make_account: MakeAccount, reader: _Reader
) -> None:
    """The one-time admission is checked where the row is reserved, not before a long wait."""
    await make_account("acc-1")
    await mark_history_read("acc-1", "2026-11-16T12:00:00+00:00")
    account = (await _history.list_accounts()).accounts[0]
    assert await _history._wait_for_budget(account) is None
    assert reader.calls == []
