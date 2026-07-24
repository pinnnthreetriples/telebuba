"""Observable failure contracts for campaign onboarding."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from core.config import settings
from core.db import (
    assign_account_to_campaign,
    create_account,
    create_campaign,
    link_channel_to_campaign,
    upsert_readiness,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.spam_status import SpamStatusVerdict
from schemas.telegram_actions import ActionResult, JoinDiscussionGroup, LinkedDiscussionGroupResult
from services.neurocomment import _seams, onboarding
from tests.services.neurocomment.onboarding_support import _no_sleep

if TYPE_CHECKING:
    from schemas.telegram_actions import TelegramAction, TelegramReadAction

pytestmark = pytest.mark.usefixtures("isolate_onboarding")


@pytest.mark.asyncio
async def test_resolve_failure_is_isolated_with_actionable_audit_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve = AsyncMock(side_effect=TimeoutError("telegram did not answer"))
    audit = AsyncMock()
    monkeypatch.setattr(onboarding, "_resolve_linked_group", resolve)
    monkeypatch.setattr(onboarding, "log_event", audit)

    result = await onboarding._safe_resolve("account-7", "@news")

    assert result is None
    resolve.assert_awaited_once_with("account-7", "@news")
    audit.assert_awaited_once_with(
        "ERROR",
        "neurocomment_onboard_resolve_failed",
        account_id="account-7",
        extra={"channel": "@news", "error_type": "TimeoutError"},
    )


async def _campaign_with_accounts(*account_ids: str, channel: str = "@news") -> str:
    for account_id in account_ids:
        await create_account(
            AccountCreate(account_id=account_id, label=account_id, session_name=account_id)
        )
    campaign = await create_campaign(CampaignCreate(name="Observed", prompt="reply"))
    await link_channel_to_campaign(campaign.campaign_id, channel)
    for account_id in account_ids:
        await assign_account_to_campaign(campaign.campaign_id, account_id)
    return campaign.campaign_id


@pytest.mark.asyncio
async def test_public_campaign_reports_complete_pair_lifecycle_and_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The progress stream mirrors paced joins and their persisted outcomes."""
    campaign_id = await _campaign_with_accounts("account-a", "account-b")
    monkeypatch.setattr(settings.neurocomment, "challenge_solver_enabled", False)
    reads: list[tuple[str, TelegramReadAction]] = []
    joins: list[tuple[str, str]] = []
    probes: list[tuple[str, bool]] = []
    sleeps: list[float] = []

    async def read(account_id: str, action: TelegramReadAction) -> LinkedDiscussionGroupResult:
        reads.append((account_id, action))
        return LinkedDiscussionGroupResult(linked_chat_id=700, comments_enabled=True)

    async def execute(account_id: str, action: TelegramAction) -> ActionResult:
        assert isinstance(action, JoinDiscussionGroup)
        joins.append((account_id, action.channel))
        if account_id == "account-b":
            return ActionResult(
                status="failed",
                action_type=action.action_type,
                account_id=account_id,
                error_type="InviteHashExpiredError",
            )
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    async def probe(account_id: str, *, force: bool) -> SpamStatusVerdict:
        probes.append((account_id, force))
        return SpamStatusVerdict(
            account_id=account_id,
            status="clean",
            checked_at="2026-07-18T00:00:00",
        )

    monkeypatch.setattr(_seams, "execute_read", read)
    monkeypatch.setattr(_seams, "execute", execute)
    monkeypatch.setattr(_seams, "refresh_spam_status", probe)
    monkeypatch.setattr(_seams.rng, "uniform", lambda _lo, _hi: 0.25)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep(sleeps))
    events = []

    result = await asyncio.wait_for(
        onboarding.onboard_campaign(campaign_id, on_progress=events.append),
        timeout=0.5,
    )

    assert reads[0][0] == "account-a"
    assert joins == [("account-a", "@news"), ("account-b", "@news")]
    assert probes == [("account-a", False), ("account-b", False)]
    assert sleeps == [0.25]
    assert [(outcome.account_id, outcome.state, outcome.reason) for outcome in result.outcomes] == [
        ("account-a", "ready", None),
        ("account-b", "failed", "InviteHashExpiredError"),
    ]
    assert [
        (
            event.code,
            event.account_id,
            event.channel,
            event.delay_seconds,
            event.state,
            event.reason,
        )
        for event in events
    ] == [
        ("onboarding_started", None, None, None, None, None),
        ("spam_probe_started", "account-a", None, None, None, None),
        ("spam_probe_started", "account-b", None, None, None, None),
        ("channel_resolving", None, "@news", None, None, None),
        ("channel_resolved", None, "@news", None, None, None),
        ("pair_joining", "account-a", "@news", None, None, None),
        ("pair_result", "account-a", "@news", None, "ready", None),
        ("pair_join_delay", None, "@news", 0.25, None, None),
        ("pair_joining", "account-b", "@news", None, None, None),
        (
            "pair_result",
            "account-b",
            "@news",
            None,
            "failed",
            "InviteHashExpiredError",
        ),
        ("onboarding_finished", None, None, None, None, None),
    ]
    assert (events[0].account_count, events[0].channel_count) == (2, 1)
    assert (events[-1].ready_count, events[-1].total_count) == (1, 2)


@pytest.mark.asyncio
async def test_public_campaign_reports_ready_channel_without_gateway_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = await _campaign_with_accounts("ready-account", channel="@ready")
    await upsert_readiness("ready-account", "@ready", joined=True, captcha_passed=True, ready=True)
    read = AsyncMock()
    execute = AsyncMock()
    monkeypatch.setattr(_seams, "execute_read", read)
    monkeypatch.setattr(_seams, "execute", execute)
    events = []

    result = await asyncio.wait_for(
        onboarding.onboard_campaign(campaign_id, on_progress=events.append),
        timeout=0.5,
    )

    read.assert_not_awaited()
    execute.assert_not_awaited()
    assert [(item.account_id, item.channel, item.state) for item in result.outcomes] == [
        ("ready-account", "@ready", "ready")
    ]
    ready_event = next(event for event in events if event.code == "channel_all_ready")
    assert ready_event.channel == "@ready"


@pytest.mark.asyncio
async def test_public_campaign_treats_disabled_comments_as_non_joinable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = await _campaign_with_accounts("account-a", channel="@closed")
    execute = AsyncMock()

    async def read(_account_id: str, _action: TelegramReadAction) -> LinkedDiscussionGroupResult:
        return LinkedDiscussionGroupResult(linked_chat_id=900, comments_enabled=False)

    monkeypatch.setattr(_seams, "execute_read", read)
    monkeypatch.setattr(_seams, "execute", execute)
    events = []

    result = await asyncio.wait_for(
        onboarding.onboard_campaign(campaign_id, on_progress=events.append),
        timeout=0.5,
    )

    execute.assert_not_awaited()
    assert [(item.account_id, item.state) for item in result.outcomes] == [
        ("account-a", "comments_off")
    ]
    off_event = next(event for event in events if event.code == "channel_comments_off")
    assert off_event.channel == "@closed"
