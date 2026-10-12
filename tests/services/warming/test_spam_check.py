"""The cycle's scheduled @SpamBot step (``services.warming._spam_check``)."""

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
from schemas.spam_status import SpamStatusKind, SpamStatusVerdict
from schemas.warming import WarmingStateWrite
from services import warming
from services.warming import _seams
from services.warming._spam_check import _gate_spam_check, check_offset_hours, spam_check_due
from tests.services.warming._support import _no_quiet_days, _Recorder, _seed_warming_account

if TYPE_CHECKING:
    from schemas.warming import WarmingStateRecord

_NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


class _Probe:
    """Stands in for ``services.spam_status.refresh_spam_status`` behind the seam."""

    def __init__(self, status: SpamStatusKind) -> None:
        self.status: SpamStatusKind = status
        self.forces: list[bool] = []

    async def __call__(self, account_id: str, *, force: bool = False) -> SpamStatusVerdict:
        self.forces.append(force)
        return SpamStatusVerdict(
            account_id=account_id,
            status=self.status,
            checked_at=datetime.now(UTC).isoformat(),
        )


def _probe(monkeypatch: pytest.MonkeyPatch, status: SpamStatusKind) -> _Probe:
    probe = _Probe(status)
    monkeypatch.setattr(_seams, "_refresh_spam_status", probe)
    return probe


async def _seed(
    *,
    started_hours_ago: float = 1.0,
    run_id: str | None = None,
    state: str = "sleeping",
) -> WarmingStateRecord:
    await create_account(AccountCreate(account_id="acc-1"))
    started = datetime.now(UTC) - timedelta(hours=started_hours_ago)
    write = await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state=state,  # ty: ignore[invalid-argument-type]
            started_at=started.isoformat(),
            run_id=run_id,
        ),
    )
    return write.record


async def _reading(status: SpamStatusKind, hours_ago: float) -> SpamStatusVerdict:
    checked = datetime.now(UTC) - timedelta(hours=hours_ago)
    return await upsert_spam_status(
        SpamStatusVerdict(account_id="acc-1", status=status, checked_at=checked.isoformat()),
    )


def _record(**overrides: object) -> WarmingStateRecord:
    from schemas.warming import WarmingStateRecord  # noqa: PLC0415

    fields: dict[str, object] = {
        "account_id": "acc-1",
        "state": "sleeping",
        "updated_at": _NOW.isoformat(),
        "started_at": (_NOW - timedelta(days=5)).isoformat(),
    }
    fields.update(overrides)
    return WarmingStateRecord.model_validate(fields)


def _cached(status: SpamStatusKind, hours_ago: float) -> SpamStatusVerdict:
    checked = (_NOW - timedelta(hours=hours_ago)).isoformat()
    return SpamStatusVerdict(account_id="acc-1", status=status, checked_at=checked)


def test_no_reading_unknown_reading_or_unreadable_streak_are_due() -> None:
    assert spam_check_due(None, _record(), _NOW)
    assert spam_check_due(_cached("unknown", 1), _record(), _NOW)
    assert spam_check_due(_cached("clean", 1), _record(spam_unknown_streak=1), _NOW)
    assert spam_check_due(
        SpamStatusVerdict(account_id="acc-1", status="clean", checked_at="garbage"),
        _record(),
        _NOW,
    )


def test_first_cycle_of_a_stint_asks_unless_the_reading_is_still_fresh() -> None:
    stint = _record(started_at=(_NOW - timedelta(hours=1)).isoformat())
    ttl = settings.warming.spam_status_ttl_hours

    assert spam_check_due(_cached("clean", ttl + 1), stint, _NOW)
    # The cache stays respected: a reading inside the TTL serves the new stint too.
    assert not spam_check_due(_cached("clean", ttl - 1), stint, _NOW)


def test_offset_is_stable_per_account_and_within_the_jitter() -> None:
    jitter = settings.warming.spam_check_jitter_hours
    offsets = {check_offset_hours(f"acc-{index}") for index in range(50)}

    assert check_offset_hours("acc-1") == check_offset_hours("acc-1")
    assert all(-jitter <= offset <= jitter for offset in offsets)
    assert len(offsets) == 50
    assert min(offsets) < 0 < max(offsets)


def test_cadence_is_the_interval_shifted_by_the_account_offset() -> None:
    due_at = settings.warming.spam_check_interval_hours + check_offset_hours("acc-1")

    assert not spam_check_due(_cached("clean", due_at - 0.1), _record(), _NOW)
    assert spam_check_due(_cached("clean", due_at + 0.1), _record(), _NOW)
    # The whole 72 ± 12 h window, whatever this account's offset is.
    assert not spam_check_due(_cached("clean", 59.9), _record(), _NOW)
    assert spam_check_due(_cached("clean", 84.1), _record(), _NOW)


@pytest.mark.asyncio
async def test_a_fresh_reading_does_not_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = _probe(monkeypatch, "clean")
    record = await _seed(started_hours_ago=48)
    await _reading("clean", hours_ago=10)

    assert await _gate_spam_check("acc-1", record, datetime.now(UTC), run_id=None) is None
    assert probe.forces == []


@pytest.mark.asyncio
async def test_first_cycle_asks_and_a_clean_answer_lets_the_cycle_go_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _probe(monkeypatch, "clean")
    record = await _seed()

    assert await _gate_spam_check("acc-1", record, datetime.now(UTC), run_id=None) is None
    assert probe.forces == [False]


@pytest.mark.asyncio
async def test_a_cached_unknown_is_asked_again_not_served(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _probe(monkeypatch, "clean")
    record = await _seed()
    await _reading("unknown", hours_ago=1)

    await _gate_spam_check("acc-1", record, datetime.now(UTC), run_id=None)

    assert probe.forces == [True]


@pytest.mark.asyncio
async def test_limited_goes_to_quarantine_not_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _probe(monkeypatch, "limited")
    record = await _seed(run_id="run-1")
    now = datetime.now(UTC)

    result = await _gate_spam_check("acc-1", record, now, run_id="run-1")

    assert result is not None
    assert result.status == "skipped"
    assert result.detail == "spam limited"
    stored = await fetch_warming_state("acc-1")
    assert stored is not None
    assert stored.state == "quarantine"
    assert stored.last_event == "spam_check_limited"
    assert stored.next_run_at is not None
    rest = datetime.fromisoformat(stored.next_run_at) - now
    assert rest == timedelta(hours=settings.warming.quarantine_hours)


@pytest.mark.asyncio
async def test_unreadable_answers_count_a_streak_and_a_reading_resets_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _probe(monkeypatch, "unknown")
    await _seed(run_id="run-1")

    for expected in (1, 2, 3):
        record = await fetch_warming_state("acc-1")
        result = await _gate_spam_check("acc-1", record, datetime.now(UTC), run_id="run-1")
        assert result is None, "an unreadable answer must not stop the cycle"
        stored = await fetch_warming_state("acc-1")
        assert stored is not None
        assert stored.spam_unknown_streak == expected
        assert stored.state == "sleeping"

    probe.status = "clean"
    record = await fetch_warming_state("acc-1")
    await _gate_spam_check("acc-1", record, datetime.now(UTC), run_id="run-1")

    stored = await fetch_warming_state("acc-1")
    assert stored is not None
    assert stored.spam_unknown_streak == 0


@pytest.mark.asyncio
async def test_a_retired_generation_does_not_count_the_streak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _probe(monkeypatch, "unknown")
    record = await _seed(run_id="run-new")

    result = await _gate_spam_check("acc-1", record, datetime.now(UTC), run_id="run-old")

    assert result is not None
    assert result.detail == "stale run"
    stored = await fetch_warming_state("acc-1")
    assert stored is not None
    assert stored.spam_unknown_streak == 0


@pytest.mark.asyncio
async def test_the_streak_write_never_touches_an_idle_row() -> None:
    await _seed(state="idle")

    assert await record_spam_check_outcome("acc-1", readable=False, expected_run_id=None) is None


@pytest.mark.asyncio
async def test_board_card_warns_after_the_configured_streak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _probe(monkeypatch, "unknown")
    await _seed()
    warn_at = settings.warming.spam_unknown_warn_streak

    for _ in range(warn_at - 1):
        await record_spam_check_outcome("acc-1", readable=False, expected_run_id=None)
    before = {card.account_id: card for card in (await warming.load_board()).warming}
    await record_spam_check_outcome("acc-1", readable=False, expected_run_id=None)
    after = {card.account_id: card for card in (await warming.load_board()).warming}

    assert before["acc-1"].spam_check_warning is False
    assert after["acc-1"].spam_check_warning is True


@pytest.mark.asyncio
async def test_iteration_quarantines_a_limited_account_before_any_telegram_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_quiet_days(monkeypatch)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    probe = _probe(monkeypatch, "limited")
    await _seed_warming_account(run_id="run-1")

    result = await warming.run_loop_iteration("acc-1", run_id="run-1")

    assert result.detail == "spam limited"
    assert probe.forces == [False]
    assert recorder.actions == []
    stored = await fetch_warming_state("acc-1")
    assert stored is not None
    assert stored.state == "quarantine"
