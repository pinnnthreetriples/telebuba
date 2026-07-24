"""Behavioural contracts for a channel read followed by an optional reaction."""

from __future__ import annotations

import pytest

from schemas.telegram_actions import ActionResult, TelegramAction
from services.warming import _cycle, _seams


def _result(
    action_type: str,
    status: str,
    *,
    message_id: int | None = None,
) -> ActionResult:
    return ActionResult.model_validate(
        {
            "account_id": "acc-1",
            "action_type": action_type,
            "status": status,
            "message_id": message_id,
            "flood_wait_seconds": 90 if "wait" in status else None,
        }
    )


@pytest.fixture(autouse=True)
def no_human_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep reaction-gate tests independent from delay entropy."""

    async def no_pause(_minimum: float, _maximum: float) -> None:
        return None

    monkeypatch.setattr(_cycle, "_human_pause", no_pause)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("read_status", "reactions_enabled", "remaining_actions", "expected"),
    [
        ("failed", True, None, (0, 0, 1, 1, None)),
        ("unavailable", True, None, (0, 0, 1, 1, None)),
        ("peer_flood", True, None, (0, 0, 0, 1, "peer_flood")),
        ("premium_wait", True, None, (0, 0, 0, 1, "premium_wait")),
        ("ok", False, None, (1, 0, 0, 1, None)),
        ("ok", True, 1, (1, 0, 0, 1, None)),
    ],
)
async def test_reaction_gate_does_not_consume_entropy_when_reaction_is_impossible(
    monkeypatch: pytest.MonkeyPatch,
    read_status: str,
    reactions_enabled: bool,  # noqa: FBT001 - table input describes the public setting.
    remaining_actions: int | None,
    expected: tuple[int, int, int, int, str | None],
) -> None:
    calls: list[str] = []
    random_calls = 0

    async def execute(_account_id: str, action: TelegramAction) -> ActionResult:
        calls.append(action.action_type)
        return _result(action.action_type, read_status)

    def draw() -> float:
        nonlocal random_calls
        random_calls += 1
        return 0.0

    monkeypatch.setattr(_seams, "execute", execute)
    monkeypatch.setattr(_seams.rng, "random", draw)

    outcome = await _cycle._read_and_react(
        "acc-1",
        "channel_one",
        reactions_enabled=reactions_enabled,
        reaction_probability=0.5,
        attempts_so_far=0,
        remaining_actions=remaining_actions,
    )

    flood_status = outcome.flood.status if outcome.flood is not None else None
    assert (outcome.reads, outcome.reactions, outcome.failures, outcome.attempts, flood_status) == (
        expected
    )
    assert calls == ["read_channel"]
    assert random_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("draw", "expected_actions", "expected_reactions"),
    [
        (0.49, ["read_channel", "react_to_post"], 1),
        (0.50, ["read_channel"], 0),
    ],
)
async def test_reaction_gate_draws_exactly_once_per_eligible_read(
    monkeypatch: pytest.MonkeyPatch,
    draw: float,
    expected_actions: list[str],
    expected_reactions: int,
) -> None:
    calls: list[str] = []
    random_calls = 0

    async def execute(_account_id: str, action: TelegramAction) -> ActionResult:
        calls.append(action.action_type)
        return _result(
            action.action_type,
            "ok",
            message_id=42 if action.action_type == "react_to_post" else None,
        )

    def random_draw() -> float:
        nonlocal random_calls
        random_calls += 1
        return draw

    monkeypatch.setattr(_seams, "execute", execute)
    monkeypatch.setattr(_seams.rng, "random", random_draw)

    outcome = await _cycle._read_and_react(
        "acc-1",
        "channel_one",
        reactions_enabled=True,
        reaction_probability=0.5,
        attempts_so_far=0,
        remaining_actions=None,
    )

    assert calls == expected_actions
    assert random_calls == 1
    assert outcome.reads == 1
    assert outcome.reactions == expected_reactions
    assert outcome.attempts == len(expected_actions)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("react_status", "message_id", "expected"),
    [
        ("ok", 42, (1, 0, None)),
        ("ok", None, (0, 0, None)),
        ("failed", None, (0, 1, None)),
        ("unavailable", None, (0, 1, None)),
        ("peer_flood", None, (0, 0, "peer_flood")),
        ("slow_mode_wait", None, (0, 0, "slow_mode_wait")),
    ],
)
async def test_reaction_outcome_preserves_landed_failure_and_halt_semantics(
    monkeypatch: pytest.MonkeyPatch,
    react_status: str,
    message_id: int | None,
    expected: tuple[int, int, str | None],
) -> None:
    async def execute(_account_id: str, action: TelegramAction) -> ActionResult:
        if action.action_type == "read_channel":
            return _result(action.action_type, "ok")
        return _result(action.action_type, react_status, message_id=message_id)

    monkeypatch.setattr(_seams, "execute", execute)
    monkeypatch.setattr(_seams.rng, "random", lambda: 0.0)

    outcome = await _cycle._read_and_react(
        "acc-1",
        "channel_one",
        reactions_enabled=True,
        reaction_probability=1.0,
        attempts_so_far=0,
        remaining_actions=None,
    )

    flood_status = outcome.flood.status if outcome.flood is not None else None
    assert (outcome.reactions, outcome.failures, flood_status) == expected
    assert outcome.reads == 1
    assert outcome.attempts == 2
