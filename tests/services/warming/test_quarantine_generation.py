"""Generation-safe quarantine recovery contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.config import settings
from schemas.spam_status import SpamStatusKind, SpamStatusVerdict
from schemas.warming import WarmingStateRecord, WarmingStateWriteResult
from services.warming import _loop, _seams


def _record(*, count: int = 0, state: str = "quarantine") -> WarmingStateRecord:
    return WarmingStateRecord.model_validate(
        {
            "account_id": "acc-1",
            "state": state,
            "updated_at": "2026-07-17T12:00:00+00:00",
            "quarantine_count": count,
            "run_id": "generation-a",
        }
    )


def _write_result(*, applied: bool, record: WarmingStateRecord) -> WarmingStateWriteResult:
    return WarmingStateWriteResult(record=record, applied=applied)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verdict", "count", "max_repeats"),
    [("clean", 1, 3), ("limited", 0, 3), ("limited", 2, 3)],
    ids=["recover", "extend", "exhaust"],
)
async def test_final_quarantine_write_losing_generation_is_silent(
    monkeypatch: pytest.MonkeyPatch,
    verdict: SpamStatusKind,
    count: int,
    max_repeats: int,
) -> None:
    """A stop/restart after the probe wins before outcome or audit emission."""
    monkeypatch.setattr(settings.warming, "quarantine_max_repeats", max_repeats)
    record = _record(count=count)
    writes: list[dict[str, object]] = []

    async def set_state(_account_id: str, state: str, **kwargs: object) -> WarmingStateWriteResult:
        writes.append({"state": state, **kwargs})
        return _write_result(applied=len(writes) == 1, record=record)

    async def refresh(account_id: str, *, force: bool = False) -> SpamStatusVerdict:
        assert force is True
        return SpamStatusVerdict(
            account_id=account_id,
            status=verdict,
            checked_at="2026-07-17T12:00:00+00:00",
        )

    events: list[str] = []

    async def log(_level: str, event: str, **_kwargs: object) -> None:
        events.append(event)

    monkeypatch.setattr(_loop, "_set_state", set_state)
    monkeypatch.setattr(_seams, "refresh_spam_status", refresh)
    monkeypatch.setattr(_loop, "log_event", log)

    result = await _loop._recover_from_quarantine(
        "acc-1", record, datetime(2026, 7, 17, 12, tzinfo=UTC), run_id="generation-a"
    )

    assert result.status == "skipped"
    assert result.detail == "stale run"
    assert len(writes) == 2
    assert writes[0]["last_event"] == "quarantine_probe_started"
    assert writes[1]["expected_run_id"] == "generation-a"
    assert events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verdict", "count", "expected_state", "expected_event", "expected_detail"),
    [
        ("clean", 2, "sleeping", "warming_quarantine_recovered", "recovered"),
        ("limited", 0, "quarantine", "warming_quarantine_extended", "quarantine extended"),
        ("limited", 2, "error", "warming_quarantine_exhausted", "quarantine exhausted"),
    ],
)
async def test_quarantine_outcome_is_emitted_only_after_applied_write(  # noqa: PLR0913
    monkeypatch: pytest.MonkeyPatch,
    verdict: SpamStatusKind,
    count: int,
    expected_state: str,
    expected_event: str,
    expected_detail: str,
) -> None:
    monkeypatch.setattr(settings.warming, "quarantine_max_repeats", 3)
    record = _record(count=count)
    writes: list[dict[str, object]] = []

    async def set_state(_account_id: str, state: str, **kwargs: object) -> WarmingStateWriteResult:
        writes.append({"state": state, **kwargs})
        return _write_result(applied=True, record=record)

    async def refresh(account_id: str, *, force: bool = False) -> SpamStatusVerdict:
        assert force is True
        return SpamStatusVerdict(
            account_id=account_id,
            status=verdict,
            checked_at="2026-07-17T12:00:00+00:00",
        )

    events: list[str] = []

    async def log(_level: str, event: str, **_kwargs: object) -> None:
        events.append(event)

    monkeypatch.setattr(_loop, "_set_state", set_state)
    monkeypatch.setattr(_seams, "refresh_spam_status", refresh)
    monkeypatch.setattr(_loop, "log_event", log)

    result = await _loop._recover_from_quarantine(
        "acc-1", record, datetime(2026, 7, 17, 12, tzinfo=UTC), run_id="generation-a"
    )

    assert result.detail == expected_detail
    assert writes[-1]["state"] == expected_state
    assert events == [expected_event]
    if verdict == "clean":
        assert writes[-1]["quarantine_count"] == 0
        assert writes[-1]["last_error"] is None
    else:
        assert writes[-1]["quarantine_count"] == count + 1


@pytest.mark.asyncio
async def test_stale_generation_before_probe_performs_no_external_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record()
    probes = 0

    async def rejected(*_args: object, **_kwargs: object) -> WarmingStateWriteResult:
        return _write_result(applied=False, record=record)

    async def refresh(*_args: object, **_kwargs: object) -> SpamStatusVerdict:
        nonlocal probes
        probes += 1
        message = "stale generation must not probe SpamBot"
        raise AssertionError(message)

    monkeypatch.setattr(_loop, "_set_state", rejected)
    monkeypatch.setattr(_seams, "refresh_spam_status", refresh)

    result = await _loop._recover_from_quarantine(
        "acc-1", record, datetime(2026, 7, 17, 12, tzinfo=UTC), run_id="generation-a"
    )

    assert result.detail == "stale run"
    assert probes == 0
