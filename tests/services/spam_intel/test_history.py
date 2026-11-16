"""The history sweep: eligibility, pacing, storage, and that it never runs unless switched on."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import get_spam_status
from core.repositories.spam_probe_ledger import fetch_probe_state, load_budget_snapshot
from core.repositories.spam_reply_samples import list_reply_samples
from schemas.spam_status import SpamBotHistory
from services import _account_owner
from services.spam_intel import _history

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.services.spam_intel.conftest import Clock, Events

    MakeAccount = Callable[..., Awaitable[None]]

_LONG_AGO = "2000-01-01T00:00:00+00:00"


class _Reader:
    """Stands in for ``read_spambot_history``."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.texts: dict[str, list[str]] = {}
        self.failing: set[str] = set()

    async def __call__(self, account_id: str, _limit: int) -> SpamBotHistory:
        self.calls.append(account_id)
        if account_id in self.failing:
            return SpamBotHistory(account_id=account_id, error="TimeoutError")
        return SpamBotHistory(account_id=account_id, texts=self.texts.get(account_id, []))


@pytest.fixture(autouse=True)
def reader(monkeypatch: pytest.MonkeyPatch) -> _Reader:
    fake = _Reader()
    monkeypatch.setattr(_history, "read_spambot_history", fake)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(_history, "_sleep", _no_sleep)
    return fake


@pytest.mark.asyncio
async def test_it_reads_each_eligible_account_once_and_keeps_redacted_samples(
    make_account: MakeAccount, reader: _Reader
) -> None:
    await make_account("acc-1")
    await make_account("acc-2")
    reader.texts = {"acc-1": ["Good news, no limits are currently applied.", "an older reply"]}
    assert await _history.run_history_sweep() == 2
    assert sorted(reader.calls) == ["acc-1", "acc-2"]
    samples = await list_reply_samples()
    assert {s.source for s in samples} == {"history"}
    assert len(samples) == 2
    state = await fetch_probe_state("acc-1")
    assert state is not None
    assert state.history_read_at is not None
    # A second sweep finds nothing left to read.
    reader.calls.clear()
    assert await _history.run_history_sweep() == 0
    assert reader.calls == []


@pytest.mark.asyncio
async def test_it_never_touches_the_stored_verdict(
    make_account: MakeAccount, reader: _Reader
) -> None:
    await make_account("acc-1")
    reader.texts = {"acc-1": ["Your account is now limited until 29 Oct 2026."]}
    await _history.run_history_sweep()
    assert await get_spam_status("acc-1") is None


@pytest.mark.asyncio
async def test_accounts_that_cannot_be_read_are_skipped(
    make_account: MakeAccount, reader: _Reader
) -> None:
    await make_account("alive")
    await make_account("no-proxy", proxy=None)
    await make_account("dead", status="unauthorized")
    await make_account("busy")
    assert _account_owner.try_claim("busy", "warming", "run-1") is None
    assert await _history.run_history_sweep() == 1
    assert reader.calls == ["alive"]


@pytest.mark.asyncio
async def test_a_failed_read_is_logged_and_retried_on_the_next_run(
    make_account: MakeAccount, reader: _Reader, events: Events
) -> None:
    await make_account("acc-1")
    reader.failing = {"acc-1"}
    assert await _history.run_history_sweep() == 0
    state = await fetch_probe_state("acc-1")
    assert state is None or state.history_read_at is None
    assert events.of("spam_intel_history_read")[0]["ok"] is False
    reader.failing = set()
    assert await _history.run_history_sweep() == 1


@pytest.mark.asyncio
async def test_a_read_is_a_history_row_that_spaces_the_proxy_but_spends_no_probe_budget(
    make_account: MakeAccount,
) -> None:
    await make_account("acc-1")
    await _history.run_history_sweep()
    snapshot = await load_budget_snapshot("acc-1", None, _LONG_AGO)
    assert snapshot.fleet_count_24h == 0
    assert snapshot.last_fleet_started_at is not None


@pytest.mark.asyncio
async def test_two_accounts_behind_one_proxy_wait_for_the_spacing(
    make_account: MakeAccount,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float] = []

    async def _advance(seconds: float) -> None:
        waits.append(seconds)
        clock.advance(seconds=seconds)

    monkeypatch.setattr(_history, "_sleep", _advance)
    monkeypatch.setattr(settings.spam_intel, "proxy_spacing_minutes", 10.0)
    await make_account("acc-1", proxy="shared")
    await make_account("acc-2", proxy="shared")
    assert await _history.run_history_sweep() == 2
    assert any(abs(w - 600.0) < 5.0 for w in waits)  # the second waited out the proxy spacing


@pytest.mark.asyncio
async def test_a_wait_longer_than_the_cap_skips_the_account_this_run(
    make_account: MakeAccount,
    reader: _Reader,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.spam_intel, "proxy_spacing_minutes", 600.0)
    await make_account("acc-1", proxy="shared")
    await make_account("acc-2", proxy="shared")
    assert await _history.run_history_sweep() == 1
    assert len(reader.calls) == 1


@pytest.mark.asyncio
async def test_the_sweep_does_not_start_unless_switched_on(monkeypatch: pytest.MonkeyPatch) -> None:
    await _history.start_history_sweep()
    assert _history._task.task is None
    monkeypatch.setattr(settings.spam_intel, "history_sweep", True)
    monkeypatch.setattr(settings.spam_intel, "enabled", False)
    await _history.start_history_sweep()
    assert _history._task.task is None


@pytest.mark.asyncio
async def test_a_switched_on_sweep_runs_as_a_task_and_shuts_down_cleanly(
    make_account: MakeAccount,
    reader: _Reader,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.spam_intel, "history_sweep", True)
    await make_account("acc-1")
    await _history.start_history_sweep()
    task = _history._task.task
    assert task is not None
    await task
    assert reader.calls == ["acc-1"]
    await _history.shutdown_history_sweep()
    assert _history._task.task is None
    await _history.shutdown_history_sweep()  # idempotent


@pytest.mark.asyncio
async def test_a_crashing_sweep_is_logged_and_does_not_escape(
    events: Events, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom() -> int:
        msg = "db gone"
        raise RuntimeError(msg)

    monkeypatch.setattr(_history, "run_history_sweep", _boom)
    await _history._run()
    assert events.of("spam_intel_history_sweep_failed") == [{"error_type": "RuntimeError"}]
