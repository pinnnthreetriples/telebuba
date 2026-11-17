"""Shared doubles for the appeal tests: one clock for the appeal layer, its events, a limit."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from core.db import upsert_spam_status
from schemas.spam_status import SpamStatusVerdict
from services.spam_intel import _appeal, _appeal_close

if TYPE_CHECKING:
    from tests.services.spam_intel.conftest import Clock, Events

# A fixed "today", so a dated limit is in the future whatever day the suite runs on.
TODAY = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
DATED_LIMIT = "Your account is now limited until 29 Nov 2026."


@pytest.fixture(autouse=True)
def appeal_clock(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> Clock:
    """The appeal layer's filing and closing clock follows the suite's moving clock."""
    clock.at = TODAY
    monkeypatch.setattr(_appeal_close, "now", clock.now)
    return clock


@pytest.fixture(autouse=True)
def appeal_events(monkeypatch: pytest.MonkeyPatch, events: Events) -> Events:
    """The appeal modules' ``log_event`` lands in the same recorder as the gateway's."""

    async def _record(
        level: str,
        event: str,
        *,
        account_id: str | None = None,  # noqa: ARG001
        extra: dict[str, object] | None = None,
    ) -> None:
        events.rows.append((level, event, dict(extra or {})))

    for module in (_appeal, _appeal_close):
        monkeypatch.setattr(module, "log_event", _record)
    return events


async def store_reading(account_id: str, status: str, detail: str | None, at: datetime) -> None:
    """A real @SpamBot reading as the probe stores it (the legacy columns the lift reads)."""
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id=account_id,
            status=status,  # ty: ignore[invalid-argument-type]
            detail=detail,
            checked_at=at.isoformat(),
        )
    )


async def store_limited(account_id: str, at: datetime, detail: str | None = DATED_LIMIT) -> None:
    await store_reading(account_id, "limited", detail, at)
