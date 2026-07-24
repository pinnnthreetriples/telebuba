"""Boundary and observability contracts for neurocomment runtime maintenance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from core.config import settings
from services.neurocomment import _runtime

pytestmark = pytest.mark.usefixtures("isolate_runtime")


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz: object = None) -> _FrozenDateTime:
        assert tz is UTC
        return cls(2026, 7, 18, 12, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_stale_claim_reclaim_uses_configured_cutoff_and_logs_nonzero_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "stale_claim_reclaim_seconds", 75)
    reclaim = AsyncMock(return_value=4)
    audit = AsyncMock()
    monkeypatch.setattr(_runtime, "datetime", _FrozenDateTime)
    monkeypatch.setattr(_runtime, "reclaim_stale_claims", reclaim)
    monkeypatch.setattr(_runtime, "log_event", audit)

    await _runtime._reclaim_stale_claims_on_startup()

    expected = (_FrozenDateTime.now(UTC) - timedelta(seconds=75)).isoformat()
    reclaim.assert_awaited_once_with(expected)
    audit.assert_awaited_once_with(
        "INFO",
        "neurocomment_stale_claims_reclaimed",
        extra={"count": 4},
    )


@pytest.mark.asyncio
async def test_stale_claim_reclaim_does_not_emit_empty_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit = AsyncMock()
    monkeypatch.setattr(_runtime, "reclaim_stale_claims", AsyncMock(return_value=0))
    monkeypatch.setattr(_runtime, "log_event", audit)

    await _runtime._reclaim_stale_claims_on_startup()

    audit.assert_not_awaited()
