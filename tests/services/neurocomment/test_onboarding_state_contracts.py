"""Onboarding state-machine contracts."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from core.config import settings
from core.db import (
    assign_account_to_campaign,
    create_account,
    create_campaign,
    fetch_readiness,
    link_channel_to_campaign,
    mark_human_skipped,
    mark_pair_banned,
    update_solver_enabled,
    upsert_readiness,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.telegram_actions import ActionResult, ActionStatus, LinkedDiscussionGroupResult
from services.neurocomment import _seams, onboarding
from tests.services.neurocomment.onboarding_support import _JoinStub, _ReadStub

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
    audit = AsyncMock()
    monkeypatch.setattr(onboarding, "upsert_readiness", write)
    monkeypatch.setattr(onboarding, "log_event", audit)

    outcome = await onboarding._classify_join(
        "account", "@channel", _result(status, seconds=12), 123, solver_enabled=False
    )

    assert outcome.state == "joining"
    assert outcome.reason == f"{status}:12"
    write.assert_not_awaited()
    audit.assert_awaited_once_with(
        "INFO",
        "neurocomment_onboard_retry_later",
        account_id="account",
        extra={"channel": "@channel", "status": status},
    )


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
async def test_hard_ban_is_persisted_as_sticky_banned_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write = AsyncMock()
    mark_banned = AsyncMock()
    monkeypatch.setattr(onboarding, "upsert_readiness", write)
    monkeypatch.setattr(onboarding, "mark_pair_banned", mark_banned)

    outcome = await onboarding._classify_join(
        "account",
        "@channel",
        _result("failed", error_type="UserBannedInChannelError"),
        123,
        solver_enabled=True,
    )

    assert outcome.model_dump() == {
        "account_id": "account",
        "channel": "@channel",
        "state": "banned",
        "reason": None,
    }
    write.assert_awaited_once_with(
        "account", "@channel", joined=True, captcha_passed=False, ready=False
    )
    mark_banned.assert_awaited_once_with("account", "@channel")


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


async def _public_pair(channel: str = "@channel") -> str:
    await create_account(AccountCreate(account_id="account", session_name="account"))
    campaign = await create_campaign(CampaignCreate(name="Campaign", prompt="reply"))
    await link_channel_to_campaign(campaign.campaign_id, channel)
    return campaign.campaign_id


@pytest.mark.asyncio
@pytest.mark.parametrize("sticky_state", ["human_skipped", "banned"])
async def test_public_pair_preserves_sticky_operator_and_ban_states(
    monkeypatch: pytest.MonkeyPatch,
    sticky_state: str,
) -> None:
    await _public_pair()
    await upsert_readiness("account", "@channel", joined=True, captcha_passed=True, ready=True)
    if sticky_state == "human_skipped":
        await mark_human_skipped("account", "@channel")
    else:
        await mark_pair_banned("account", "@channel")
    read = _ReadStub(linked_chat_id=700, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)

    outcome = await asyncio.wait_for(
        onboarding.onboard_account_channel("account", "@channel"),
        timeout=0.5,
    )

    assert outcome.model_dump() == {
        "account_id": "account",
        "channel": "@channel",
        "state": sticky_state,
        "reason": None,
    }
    assert read.calls[0][0] == "account"
    assert join.calls == []
    persisted = await fetch_readiness("account", "@channel")
    assert persisted is not None
    assert persisted.ready is False
    assert (persisted.human_skipped, persisted.banned) == (
        sticky_state == "human_skipped",
        sticky_state == "banned",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("comments_enabled", "linked_chat_id"),
    [(False, 700), (True, None)],
)
async def test_public_pair_requires_both_comments_and_linked_group(
    monkeypatch: pytest.MonkeyPatch,
    comments_enabled: bool,  # noqa: FBT001
    linked_chat_id: int | None,
) -> None:
    await _public_pair()
    join = AsyncMock()
    monkeypatch.setattr(
        _seams,
        "execute_read",
        AsyncMock(
            return_value=LinkedDiscussionGroupResult(
                linked_chat_id=linked_chat_id,
                comments_enabled=comments_enabled,
            )
        ),
    )
    monkeypatch.setattr(_seams, "execute", join)

    outcome = await asyncio.wait_for(
        onboarding.onboard_account_channel("account", "@channel"),
        timeout=0.5,
    )

    assert outcome.model_dump() == {
        "account_id": "account",
        "channel": "@channel",
        "state": "comments_off",
        "reason": None,
    }
    join.assert_not_awaited()
    assert await fetch_readiness("account", "@channel") is None


@pytest.mark.asyncio
async def test_public_solver_disabled_join_persists_fully_ready_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _public_pair()
    monkeypatch.setattr(settings.neurocomment, "challenge_solver_enabled", False)
    read = _ReadStub(linked_chat_id=700, comments_enabled=True)
    join = _JoinStub()
    solver = AsyncMock()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.challenge, "solve_if_present", solver)

    outcome = await asyncio.wait_for(
        onboarding.onboard_account_channel("account", "@channel"),
        timeout=0.5,
    )

    assert outcome.state == "ready"
    solver.assert_not_awaited()
    persisted = await fetch_readiness("account", "@channel")
    assert persisted is not None
    assert (persisted.joined, persisted.captcha_passed, persisted.ready) == (
        True,
        True,
        True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("override", "global_enabled"), [(True, False), (False, True)])
async def test_public_campaign_solver_override_controls_solver_execution(
    monkeypatch: pytest.MonkeyPatch,
    override: bool,  # noqa: FBT001
    global_enabled: bool,  # noqa: FBT001
) -> None:
    campaign_id = await _public_pair()
    await assign_account_to_campaign(campaign_id, "account")
    await update_solver_enabled(campaign_id, value=override)
    monkeypatch.setattr(settings.neurocomment, "challenge_solver_enabled", global_enabled)
    read = _ReadStub(linked_chat_id=700, comments_enabled=True)
    join = _JoinStub()
    solver = AsyncMock(return_value="no_challenge")
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.challenge, "solve_if_present", solver)

    result = await asyncio.wait_for(onboarding.onboard_campaign(campaign_id), timeout=0.5)

    assert [(item.account_id, item.state) for item in result.outcomes] == [("account", "ready")]
    if override:
        solver.assert_awaited_once_with("account", "@channel", 700)
    else:
        solver.assert_not_awaited()
