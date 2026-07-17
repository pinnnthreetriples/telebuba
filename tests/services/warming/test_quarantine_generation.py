"""Generation-safe quarantine recovery contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import create_account, fetch_warming_state, upsert_warming_state
from schemas.accounts import AccountCreate
from schemas.spam_status import SpamStatusKind, SpamStatusVerdict
from schemas.warming import WarmingStateRecord, WarmingStateWrite, WarmingStateWriteResult
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
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="quarantine",
            quarantine_count=count,
            run_id="generation-a",
            last_event="quarantined",
        )
    )
    record = await fetch_warming_state("acc-1")
    assert record is not None

    async def refresh(account_id: str, *, force: bool = False) -> SpamStatusVerdict:
        assert force is True
        # The pre-probe CAS has landed. A concurrent stop/restart now takes the
        # row before the recovery branch attempts its final outcome write.
        await upsert_warming_state(
            WarmingStateWrite(
                account_id=account_id,
                state="idle",
                run_id="generation-b",
                last_event="queued-new-generation",
                quarantine_count=9,
            )
        )
        return SpamStatusVerdict(
            account_id=account_id,
            status=verdict,
            checked_at="2026-07-17T12:00:00+00:00",
        )

    events: list[str] = []

    async def log(_level: str, event: str, **_kwargs: object) -> None:
        events.append(event)

    monkeypatch.setattr(_seams, "refresh_spam_status", refresh)
    monkeypatch.setattr(_loop, "log_event", log)

    result = await _loop._recover_from_quarantine(
        "acc-1", record, datetime(2026, 7, 17, 12, tzinfo=UTC), run_id="generation-a"
    )

    assert result.status == "skipped"
    assert result.detail == "stale run"
    assert events == []
    current = await fetch_warming_state("acc-1")
    assert current is not None
    assert current.state == "idle"
    assert current.run_id == "generation-b"
    assert current.last_event == "queued-new-generation"
    assert current.quarantine_count == 9


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        {
            "verdict": "clean",
            "count": 2,
            "state": "sleeping",
            "event": "warming_quarantine_recovered",
            "last_event": "quarantine_recovered",
            "detail": "recovered",
            "status": "skipped",
            "level": "INFO",
        },
        {
            "verdict": "limited",
            "count": 0,
            "state": "quarantine",
            "event": "warming_quarantine_extended",
            "last_event": "quarantine_extended",
            "detail": "quarantine extended",
            "status": "skipped",
            "level": "WARNING",
        },
        {
            "verdict": "limited",
            "count": 2,
            "state": "error",
            "event": "warming_quarantine_exhausted",
            "last_event": "quarantine_exhausted",
            "detail": "quarantine exhausted",
            "status": "error",
            "level": "ERROR",
        },
    ],
    ids=["recover", "extend", "exhaust"],
)
async def test_quarantine_outcome_is_emitted_only_after_applied_write(
    monkeypatch: pytest.MonkeyPatch,
    case: dict[str, object],
) -> None:
    monkeypatch.setattr(settings.warming, "quarantine_max_repeats", 3)
    monkeypatch.setattr(settings.warming, "startup_jitter_max_seconds", 17.0)
    monkeypatch.setattr(settings.warming, "quarantine_hours", 6.0)
    verdict = case["verdict"]
    assert isinstance(verdict, str)
    count = case["count"]
    assert isinstance(count, int)
    record = _record(count=count)
    writes: list[dict[str, object]] = []

    async def set_state(_account_id: str, state: str, **kwargs: object) -> WarmingStateWriteResult:
        writes.append({"state": state, **kwargs})
        return _write_result(applied=True, record=record)

    async def refresh(account_id: str, *, force: bool = False) -> SpamStatusVerdict:
        assert force is True
        return SpamStatusVerdict(
            account_id=account_id,
            status=verdict,  # ty: ignore[invalid-argument-type]
            checked_at="2026-07-17T12:00:00+00:00",
        )

    events: list[dict[str, object]] = []

    async def log(level: str, event: str, **kwargs: object) -> None:
        events.append({"level": level, "event": event, **kwargs})

    monkeypatch.setattr(_loop, "_set_state", set_state)
    monkeypatch.setattr(_seams, "refresh_spam_status", refresh)
    monkeypatch.setattr(_loop, "log_event", log)

    now = datetime(2026, 7, 17, 12, tzinfo=UTC)
    result = await _loop._recover_from_quarantine("acc-1", record, now, run_id="generation-a")

    assert result.status == case["status"]
    assert result.detail == case["detail"]
    final = writes[-1]
    assert final["state"] == case["state"]
    assert final["last_event"] == case["last_event"]
    assert final["heartbeat_at"] == now.isoformat()
    assert final["expected_run_id"] == "generation-a"
    assert events[0]["level"] == case["level"]
    assert events[0]["event"] == case["event"]
    assert events[0]["account_id"] == "acc-1"
    if verdict == "clean":
        assert final["quarantine_count"] == 0
        assert final["last_error"] is None
        assert final["next_run_at"] == (now + timedelta(seconds=17)).isoformat()
        assert "extra" not in events[0]
    elif case["state"] == "quarantine":
        assert final["quarantine_count"] == count + 1
        assert final["next_run_at"] == (now + timedelta(hours=6)).isoformat()
        assert events[0]["extra"] == {"checks": count + 1}
    else:
        assert final["quarantine_count"] == count + 1
        assert final["last_error"] == "peer-flood not lifted after 3 checks"
        assert events[0]["extra"] == {"checks": count + 1}


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
