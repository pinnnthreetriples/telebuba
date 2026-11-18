"""A postponed or refused spam check is not an unreadable one (warming, quarantine, hand-off).

The gateway answers an ask it will not run with ``served_from="deferred"`` or ``"refused"``.
Warming's streak, the quarantine's re-check count and unreadable counter, and the hand-off's
refusals all key off an ``unknown`` verdict, so each is pinned here against both shapes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    create_account,
    fetch_warming_state,
    record_spam_check_outcome,
    upsert_spam_status,
    upsert_warming_state,
)
from schemas.accounts import AccountCreate
from schemas.spam_status import SpamStatusVerdict
from schemas.warming import WarmingStateWrite
from services import warming
from services.spam_intel import _gateway
from services.warming import _seams
from services.warming._quarantine import _recover_from_quarantine
from services.warming._spam_check import _gate_spam_check, ask_spambot

if TYPE_CHECKING:
    from schemas.spam_intel import Reason, ServedFrom
    from schemas.warming import WarmingStateRecord


def _verdict(
    served_from: ServedFrom | None,
    *,
    reason: Reason | None = None,
    retry_at: datetime | None = None,
) -> SpamStatusVerdict:
    return SpamStatusVerdict(
        account_id="acc-1",
        status="unknown",
        checked_at=datetime.now(UTC).isoformat(),
        served_from=served_from,
        reason=reason,
        retry_at=retry_at.isoformat() if retry_at else None,
    )


class _Asker:
    """The warming seam's gateway asker: records who asked, answers from a script."""

    def __init__(self, answer: SpamStatusVerdict) -> None:
        self.answer = answer
        self.callers: list[str | None] = []
        self.forces: list[bool] = []

    async def __call__(self, account_id: str, *, force: bool = False) -> SpamStatusVerdict:  # noqa: ARG002
        self.callers.append(_gateway._CALLER.get())
        self.forces.append(force)
        return self.answer


def _install(monkeypatch: pytest.MonkeyPatch, answer: SpamStatusVerdict) -> _Asker:
    asker = _Asker(answer)
    monkeypatch.setattr(_seams, "_refresh_spam_status", asker)
    return asker


async def _seed(
    state: str = "sleeping", *, run_id: str | None = None, **fields: object
) -> WarmingStateRecord:
    await create_account(AccountCreate(account_id="acc-1"))
    write = await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state=state,  # ty: ignore[invalid-argument-type]
            started_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            run_id=run_id,
            **fields,  # ty: ignore[invalid-argument-type]
        )
    )
    return write.record


@pytest.fixture(autouse=True)
def _seam_lease() -> None:
    _seams.reset_leases_for_tests()


@pytest.mark.asyncio
@pytest.mark.parametrize("served_from", ["deferred", "refused"])
async def test_an_unasked_answer_adds_nothing_to_the_streak_and_goes_on(
    monkeypatch: pytest.MonkeyPatch, served_from: ServedFrom
) -> None:
    asker = _install(monkeypatch, _verdict(served_from, reason="backoff"))
    record = await _seed(run_id="run-1")
    result = await _gate_spam_check("acc-1", record, datetime.now(UTC), run_id="run-1")
    assert result is None
    assert len(asker.forces) == 1
    stored = await fetch_warming_state("acc-1")
    assert stored is not None
    assert (stored.spam_unknown_streak, stored.state) == (0, "sleeping")


@pytest.mark.asyncio
async def test_an_unreadable_answer_that_was_really_asked_still_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _verdict("probe"))
    record = await _seed(run_id="run-1")
    await _gate_spam_check("acc-1", record, datetime.now(UTC), run_id="run-1")
    stored = await fetch_warming_state("acc-1")
    assert stored is not None
    assert stored.spam_unknown_streak == 1


@pytest.mark.asyncio
async def test_a_cached_unknown_no_longer_asks_on_every_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old rule asked each cycle; the gateway's backoff answers "later" instead."""
    asker = _install(monkeypatch, _verdict("deferred", reason="backoff"))
    await _seed(run_id="run-1")
    await _streak(2)
    record = await fetch_warming_state("acc-1")
    assert record is not None
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id="acc-1", status="unknown", checked_at=datetime.now(UTC).isoformat()
        )
    )
    for _ in range(3):
        await _gate_spam_check("acc-1", record, datetime.now(UTC), run_id="run-1")
    stored = await fetch_warming_state("acc-1")
    assert stored is not None
    assert stored.spam_unknown_streak == 2  # three cycles, still two: nothing was asked
    assert asker.forces == [True, True, True]


@pytest.mark.asyncio
async def test_the_cycle_names_itself_to_the_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    asker = _install(monkeypatch, _verdict("probe"))
    record = await _seed(run_id="run-1")
    await _gate_spam_check("acc-1", record, datetime.now(UTC), run_id="run-1")
    assert asker.callers == ["warming.cycle"]


@pytest.mark.asyncio
async def test_the_hand_off_names_itself_and_an_unasked_answer_refuses_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asker = _install(monkeypatch, _verdict("deferred", reason="proxy_spacing"))
    await _seed()
    verdict = await ask_spambot("acc-1")
    assert asker.callers == ["warming.handoff"]
    assert verdict.status == "unknown"  # the hand-off turns that into spam_check_unavailable


async def _streak(count: int) -> None:
    """Put ``count`` unreadable checks on the row (the streak is not a write field)."""
    for _ in range(count):
        await record_spam_check_outcome("acc-1", readable=False, expected_run_id=None)


async def _quarantined(**fields: object) -> WarmingStateRecord:
    return await _seed("quarantine", run_id=None, **fields)


@pytest.mark.asyncio
async def test_a_deferred_recheck_keeps_the_window_and_spends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retry = datetime.now(UTC) + timedelta(hours=2)
    asker = _install(monkeypatch, _verdict("deferred", reason="fleet_spacing", retry_at=retry))
    monkeypatch.setattr(_seams, "refresh_spam_status", asker)
    await _quarantined(quarantine_count=1)
    await _streak(1)
    record = await fetch_warming_state("acc-1")
    assert record is not None
    result = await _recover_from_quarantine("acc-1", record, datetime.now(UTC))
    assert (result.status, result.detail) == ("skipped", "recheck deferred")
    stored = await fetch_warming_state("acc-1")
    assert stored is not None
    assert stored.state == "quarantine"
    assert stored.last_event == "quarantine_recheck_deferred"
    assert stored.next_run_at == retry.isoformat()
    assert (stored.quarantine_count, stored.spam_unknown_streak) == (1, 1)
    assert asker.callers == ["warming.quarantine"]
    assert asker.forces == [True]


@pytest.mark.asyncio
async def test_a_refused_recheck_waits_in_quarantine_and_never_reaches_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-009: three ``no_proxy`` refusals asked nothing, so they cannot add up to ``error``."""
    asker = _install(monkeypatch, _verdict("refused", reason="no_proxy"))
    monkeypatch.setattr(_seams, "refresh_spam_status", asker)
    await _quarantined(quarantine_count=1)
    details = []
    for _ in range(settings.warming.quarantine_unreadable_max + 1):
        record = await fetch_warming_state("acc-1")
        assert record is not None
        before = datetime.now(UTC)
        result = await _recover_from_quarantine("acc-1", record, before)
        details.append((result.status, result.detail))
        stored = await fetch_warming_state("acc-1")
        assert stored is not None
        assert stored.state == "quarantine"
        assert stored.last_event == "quarantine_recheck_deferred"
        assert stored.last_error == "spam check refused: no_proxy"
        assert (stored.quarantine_count, stored.spam_unknown_streak) == (1, 0)
        assert stored.next_run_at is not None
        wait = datetime.fromisoformat(stored.next_run_at) - before
        assert wait == timedelta(hours=settings.warming.quarantine_refused_retry_hours)
    assert set(details) == {("skipped", "recheck deferred")}
    assert len(asker.forces) == len(details)


@pytest.mark.asyncio
async def test_a_reading_after_a_refusal_releases_and_clears_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refusal = _install(monkeypatch, _verdict("refused", reason="no_proxy"))
    monkeypatch.setattr(_seams, "refresh_spam_status", refusal)
    record = await _quarantined()
    await _recover_from_quarantine("acc-1", record, datetime.now(UTC))
    clean = SpamStatusVerdict(
        account_id="acc-1", status="clean", checked_at=datetime.now(UTC).isoformat()
    )
    refusal.answer = clean
    record = await fetch_warming_state("acc-1")
    assert record is not None
    result = await _recover_from_quarantine("acc-1", record, datetime.now(UTC))
    assert result.detail == "recovered"
    stored = await fetch_warming_state("acc-1")
    assert stored is not None
    assert (stored.state, stored.last_error) == ("sleeping", None)


@pytest.mark.asyncio
async def test_a_deferred_recheck_without_a_retry_time_falls_back_to_the_busy_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asker = _install(monkeypatch, _verdict("deferred", reason="busy"))
    monkeypatch.setattr(_seams, "refresh_spam_status", asker)
    record = await _quarantined()
    before = datetime.now(UTC)
    await _recover_from_quarantine("acc-1", record, before)
    stored = await fetch_warming_state("acc-1")
    assert stored is not None
    assert stored.next_run_at is not None
    assert datetime.fromisoformat(stored.next_run_at) > before


def test_the_package_exports_what_the_cycle_imports() -> None:
    assert callable(warming.run_loop_iteration)
