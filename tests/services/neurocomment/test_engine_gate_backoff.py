"""A write gate parks the CHANNEL even when it never issued a challenge.

Live DB: one channel forbade writes to all six accounts, 16 times over three days.
The back-off only ran when a pending challenge resolved to failed, and that channel
never sent one — so every re-onboarded pair was re-gated, each round after paying for
a generation. ``test_engine_outcomes`` covers the with-a-challenge path.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.config import settings
from core.db import list_recent_logs
from schemas.telegram_actions import NewPostEvent
from services.neurocomment import _state, engine
from tests.services.neurocomment.engine_support import (
    _CommentStub,
    _make_campaign,
    _patch_io,
)

pytestmark = pytest.mark.usefixtures("isolate_engine")


@pytest.mark.asyncio
async def test_gate_without_pending_challenge_trips_channel_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # K=1: one account can only be gated once (the gate marks its pair not-ready), so
    # the counter reaches K across accounts in production — the escalation itself is
    # covered by the challenge tests. What matters here is that a gate counts at all.
    monkeypatch.setattr(settings.neurocomment, "channel_challenge_backoff_min_failures", 1)
    await _make_campaign("@chan", "acc-1")
    _patch_io(
        monkeypatch, comment=_CommentStub(status="failed", error_type="ChatWriteForbiddenError")
    )

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert _state.is_channel_in_challenge_backoff("@chan", datetime.now(UTC)) is True

    # The cause separates "captcha the solver lost" from "channel forbids comments".
    tripped = next(
        entry
        for entry in await list_recent_logs(limit=50)
        if entry.event == "neurocomment_challenge_backoff"
    )
    assert tripped.extra["cause"] == "gate"
