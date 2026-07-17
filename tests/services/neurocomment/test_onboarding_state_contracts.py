"""Onboarding state-machine contracts."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.config import settings
from schemas.telegram_actions import ActionResult, ActionStatus
from services.neurocomment import onboarding

pytestmark = pytest.mark.usefixtures("isolate_onboarding")


def _result(
    status: ActionStatus, *, error_type: str | None = None, seconds: int | None = None
) -> ActionResult:
    return ActionResult(
        status=status,
        action_type="join_discussion_group",
        account_id="account",
        error_type=error_type,
        flood_wait_seconds=seconds,
    )


@pytest.mark.parametrize(
    ("override", "global_value", "expected"),
    [(True, False, True), (False, True, False), (None, True, True), (None, False, False)],
)
def test_campaign_solver_override_precedence(
    monkeypatch: pytest.MonkeyPatch,
    override: bool | None,  # noqa: FBT001
    global_value: bool,  # noqa: FBT001
    expected: bool,  # noqa: FBT001
) -> None:
    monkeypatch.setattr(settings.neurocomment, "challenge_solver_enabled", global_value)
    assert onboarding._effective_solver_enabled(override) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["flood_wait", "slow_mode_wait", "premium_wait", "peer_flood"])
async def test_retry_statuses_are_non_terminal_without_readiness_write(
    monkeypatch: pytest.MonkeyPatch, status: ActionStatus
) -> None:
    write = AsyncMock()
    monkeypatch.setattr(onboarding, "upsert_readiness", write)
    monkeypatch.setattr(onboarding, "log_event", AsyncMock())

    outcome = await onboarding._classify_join(
        "account", "@channel", _result(status, seconds=12), 123, solver_enabled=False
    )

    assert outcome.state == "joining"
    assert outcome.reason == f"{status}:12"
    write.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_type", "state", "joined", "captcha"),
    [
        ("InviteRequestSentError", "join_by_request", False, False),
        ("ChatWriteForbiddenError", "chat_restricted", True, False),
        ("SomeFailure", "failed", False, True),
    ],
)
async def test_terminal_join_failures_persist_distinct_sentinels(
    monkeypatch: pytest.MonkeyPatch,
    error_type: str,
    state: str,
    joined: bool,  # noqa: FBT001
    captcha: bool,  # noqa: FBT001
) -> None:
    write = AsyncMock()
    monkeypatch.setattr(onboarding, "upsert_readiness", write)

    outcome = await onboarding._classify_join(
        "account",
        "@channel",
        _result("failed", error_type=error_type),
        123,
        solver_enabled=False,
    )

    assert outcome.state == state
    write.assert_awaited_once_with(
        "account", "@channel", joined=joined, captcha_passed=captcha, ready=False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("solver_outcome", "state", "ready"),
    [
        ("give_up", "bot_challenge", False),
        ("failed", "bot_challenge", False),
        ("no_challenge", "ready", True),
        ("solved", "ready", True),
    ],
)
async def test_solver_outcomes_map_to_readiness(
    monkeypatch: pytest.MonkeyPatch,
    solver_outcome: str,
    state: str,
    ready: bool,  # noqa: FBT001
) -> None:
    monkeypatch.setattr(
        onboarding.challenge, "solve_if_present", AsyncMock(return_value=solver_outcome)
    )
    write = AsyncMock()
    monkeypatch.setattr(onboarding, "upsert_readiness", write)

    outcome = await onboarding._solve_and_record("account", "@channel", 123, solver_enabled=True)

    assert outcome.state == state
    write.assert_awaited_once_with(
        "account", "@channel", joined=True, captcha_passed=ready, ready=ready
    )
