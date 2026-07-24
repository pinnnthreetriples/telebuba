"""Result and observability contracts for completed warming cycles."""

from __future__ import annotations

from typing import Any

import pytest

from services.warming import _cycle


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("flags", "expected_status", "expected_level"),
    [
        ({}, "ok", "INFO"),
        ({"failures": 2}, "failed", "WARNING"),
        ({"failures": 2, "flooded": True}, "flood_wait", "WARNING"),
        (
            {"failures": 2, "flooded": True, "peer_flooded": True},
            "peer_flood",
            "WARNING",
        ),
    ],
)
async def test_cycle_result_reports_all_outcomes_and_status_priority(
    monkeypatch: pytest.MonkeyPatch,
    flags: dict[str, Any],
    expected_status: str,
    expected_level: str,
) -> None:
    events: list[tuple[str, str, dict[str, object]]] = []

    async def log(level: str, event: str, **kwargs: object) -> None:
        events.append((level, event, kwargs))

    monkeypatch.setattr(_cycle, "log_event", log)
    tally = _cycle._ChannelTally(
        joined=3,
        reads=5,
        reactions=2,
        attempts=12,
        flood_seconds=75,
        flood_until="2026-07-17T12:01:15+00:00",
        last_failed_action="read_channel",
        last_failed_channel="channel_one",
        **flags,
    )

    result = await _cycle._build_cycle_result("acc-1", tally, messages_sent=2)

    assert result.model_dump() == {
        "account_id": "acc-1",
        "status": expected_status,
        "channels_joined": 3,
        "channels_read": 5,
        "reactions_sent": 2,
        "messages_sent": 2,
        "detail": None,
        "flood_wait_seconds": 75,
        "flood_wait_until": "2026-07-17T12:01:15+00:00",
        "failures": flags.get("failures", 0),
        "attempted_actions": 12,
        "last_failed_action": "read_channel",
        "last_failed_channel": "channel_one",
    }
    assert events == [
        (
            expected_level,
            "warming_cycle_completed",
            {
                "account_id": "acc-1",
                "extra": {
                    "status": expected_status,
                    "joined": 3,
                    "reads": 5,
                    "reactions": 2,
                    "messages": 2,
                    "failures": flags.get("failures", 0),
                    "flood_wait_seconds": 75,
                },
            },
        )
    ]
