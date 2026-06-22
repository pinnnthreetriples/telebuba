"""Tests for ``services.neurocomment.onboarding`` — campaign pre-onboarding.

Telegram I/O (``execute`` / ``execute_read``), randomness (``rng``) and the
inter-join sleep are patched at the service seam so the flow runs with no real
network, no jitter and no waiting. Mirrors ``tests/services/test_warming.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    assign_account_to_campaign,
    configure_database,
    create_account,
    create_campaign,
    fetch_linked_group,
    fetch_readiness,
    link_channel_to_campaign,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.telegram_actions import ActionResult, LinkedDiscussionGroupResult
from services import neurocomment
from services.neurocomment import _seams, onboarding

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from schemas.telegram_actions import ActionStatus, TelegramAction, TelegramReadAction


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


class _ReadStub:
    """Returns a canned ``LinkedDiscussionGroupResult`` for every read."""

    def __init__(self, *, linked_chat_id: int | None, comments_enabled: bool) -> None:
        self.result = LinkedDiscussionGroupResult(
            linked_chat_id=linked_chat_id,
            comments_enabled=comments_enabled,
        )
        self.calls: list[tuple[str, TelegramReadAction]] = []

    async def execute_read(self, account_id: str, action: TelegramReadAction) -> object:
        self.calls.append((account_id, action))
        return self.result


class _JoinStub:
    """Returns a canned join ``ActionResult`` keyed by channel, default ok."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, TelegramAction]] = []
        self.by_channel: dict[str, ActionResult] = {}

    def set(
        self,
        channel: str,
        *,
        status: ActionStatus,
        error_type: str | None = None,
        flood_wait_seconds: int | None = None,
    ) -> None:
        self.by_channel[channel] = ActionResult(
            status=status,
            action_type="join_discussion_group",
            account_id="x",
            error_type=error_type,
            flood_wait_seconds=flood_wait_seconds,
        )

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        self.calls.append((account_id, action))
        channel = getattr(action, "channel", "")
        if channel in self.by_channel:
            return self.by_channel[channel]
        return ActionResult(
            status="ok",
            action_type=action.action_type,
            account_id=account_id,
        )


def _no_sleep(records: list[float]) -> object:
    async def _sleep(seconds: float) -> None:
        records.append(seconds)

    return _sleep


# --------------------------------------------------------------------------- #
# onboard_account_channel
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_join_ok_marks_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    read = _ReadStub(linked_chat_id=4423, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)

    outcome = await onboarding.onboard_account_channel("acc-1", "@chan")

    assert outcome.state == "ready"
    assert [a.action_type for _, a in join.calls] == ["join_discussion_group"]
    # linked-group cache + readiness both persisted
    cached = await fetch_linked_group("@chan")
    assert cached is not None
    assert cached.linked_chat_id == 4423
    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    assert readiness.joined is True
    assert readiness.captcha_passed is True
    assert readiness.ready is True


@pytest.mark.asyncio
async def test_comments_off_skips_join(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    read = _ReadStub(linked_chat_id=None, comments_enabled=False)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)

    outcome = await onboarding.onboard_account_channel("acc-1", "@silent")

    assert outcome.state == "comments_off"
    assert join.calls == []  # no join attempted
    cached = await fetch_linked_group("@silent")
    assert cached is not None
    assert cached.comments_enabled is False
    # no readiness row for comments_off
    assert await fetch_readiness("acc-1", "@silent") is None


@pytest.mark.asyncio
async def test_join_by_request_does_not_get_stuck(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    read = _ReadStub(linked_chat_id=77, comments_enabled=True)
    join = _JoinStub()
    join.set("@gated", status="failed", error_type="InviteRequestSentError")
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)

    outcome = await onboarding.onboard_account_channel("acc-1", "@gated")

    assert outcome.state == "join_by_request"
    readiness = await fetch_readiness("acc-1", "@gated")
    assert readiness is not None
    assert readiness.joined is False
    assert readiness.ready is False


@pytest.mark.asyncio
async def test_captcha_gate_detected_and_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    read = _ReadStub(linked_chat_id=88, comments_enabled=True)
    join = _JoinStub()
    join.set("@captcha", status="failed", error_type="ChatGuestSendForbiddenError")
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)

    outcome = await onboarding.onboard_account_channel("acc-1", "@captcha")

    assert outcome.state == "captcha_gated"
    readiness = await fetch_readiness("acc-1", "@captcha")
    assert readiness is not None
    assert readiness.joined is True
    assert readiness.captcha_passed is False
    assert readiness.ready is False


@pytest.mark.asyncio
async def test_flood_during_join_is_retry_later(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    read = _ReadStub(linked_chat_id=99, comments_enabled=True)
    join = _JoinStub()
    join.set("@busy", status="flood_wait", flood_wait_seconds=600)
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)

    outcome = await onboarding.onboard_account_channel("acc-1", "@busy")

    assert outcome.state == "joining"  # retry later, not terminal
    assert outcome.reason is not None
    assert "600" in outcome.reason
    # not marked ready
    readiness = await fetch_readiness("acc-1", "@busy")
    assert readiness is None or readiness.ready is False


@pytest.mark.asyncio
async def test_generic_failure_is_failed_state(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    read = _ReadStub(linked_chat_id=12, comments_enabled=True)
    join = _JoinStub()
    join.set("@boom", status="failed", error_type="ChannelPrivateError")
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)

    outcome = await onboarding.onboard_account_channel("acc-1", "@boom")

    assert outcome.state == "failed"
    assert outcome.reason == "ChannelPrivateError"
    readiness = await fetch_readiness("acc-1", "@boom")
    assert readiness is not None
    assert readiness.ready is False


# --------------------------------------------------------------------------- #
# onboard_campaign
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_campaign_iterates_pairs_with_jittered_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for acc in ("acc-1", "acc-2"):
        await create_account(AccountCreate(account_id=acc, label=acc, session_name=acc))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@one")
    await link_channel_to_campaign(campaign.campaign_id, "@two")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await assign_account_to_campaign(campaign.campaign_id, "acc-2")

    read = _ReadStub(linked_chat_id=500, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(_seams.rng, "uniform", lambda _a, _b: 42.0)
    sleeps: list[float] = []
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep(sleeps))

    result = await neurocomment.onboard_campaign(campaign.campaign_id)

    # 2 channels x 2 accounts = 4 ready outcomes
    assert result.campaign_id == campaign.campaign_id
    assert len(result.outcomes) == 4
    assert all(o.state == "ready" for o in result.outcomes)
    # jittered sleep ran between joins, never actually waiting
    assert sleeps == [42.0, 42.0, 42.0]
    nc = settings.neurocomment
    assert all(nc.join_delay_min_seconds <= s <= nc.join_delay_max_seconds for s in sleeps)


@pytest.mark.asyncio
async def test_campaign_comments_off_channel_skips_all_its_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@silent")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")

    read = _ReadStub(linked_chat_id=None, comments_enabled=False)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    result = await neurocomment.onboard_campaign(campaign.campaign_id)

    assert join.calls == []  # never joined a comments-off channel
    assert [o.state for o in result.outcomes] == ["comments_off"]


@pytest.mark.asyncio
async def test_campaign_one_failing_pair_does_not_abort_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for acc in ("acc-1", "acc-2"):
        await create_account(AccountCreate(account_id=acc, label=acc, session_name=acc))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await assign_account_to_campaign(campaign.campaign_id, "acc-2")

    read = _ReadStub(linked_chat_id=7, comments_enabled=True)

    class _Boom(_JoinStub):
        async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
            if account_id == "acc-1":
                msg = "boom"
                raise RuntimeError(msg)
            return await super().execute(account_id, action)

    join = _Boom()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    result = await neurocomment.onboard_campaign(campaign.campaign_id)

    states = {o.account_id: o.state for o in result.outcomes}
    assert states["acc-1"] == "failed"  # the raise is caught, not propagated
    assert states["acc-2"] == "ready"  # the other pair still ran


@pytest.mark.asyncio
async def test_campaign_unknown_campaign_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))
    result = await neurocomment.onboard_campaign("ghost")
    assert result.campaign_id == "ghost"
    assert result.outcomes == []


@pytest.mark.asyncio
async def test_campaign_channel_without_accounts_yields_no_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@orphan")  # no accounts assigned

    read = _ReadStub(linked_chat_id=1, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    result = await neurocomment.onboard_campaign(campaign.campaign_id)

    assert result.outcomes == []
    assert read.calls == []  # no accounts → never even resolved the group
    assert join.calls == []


# --------------------------------------------------------------------------- #
# resolve-step failure isolation (execute_read RAISES, it doesn't return)
# --------------------------------------------------------------------------- #


class _RaisingReadStub:
    """Read stub that RAISES for designated channels (simulates execute_read flood/RPC)."""

    def __init__(self, *, raise_on: set[str], linked_chat_id: int = 1) -> None:
        self.raise_on = raise_on
        self.result = LinkedDiscussionGroupResult(
            linked_chat_id=linked_chat_id,
            comments_enabled=True,
        )
        self.calls: list[tuple[str, TelegramReadAction]] = []

    async def execute_read(self, account_id: str, action: TelegramReadAction) -> object:
        self.calls.append((account_id, action))
        channel = getattr(action, "channel", "")
        if channel in self.raise_on:
            msg = f"FloodWait resolving {channel}"
            raise RuntimeError(msg)
        return self.result


@pytest.mark.asyncio
async def test_resolve_failure_is_failed_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    read = _RaisingReadStub(raise_on={"@oops"})
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)

    outcome = await onboarding.onboard_account_channel("acc-1", "@oops")

    assert outcome.state == "failed"  # resolve raise is caught, not propagated
    assert outcome.reason == "resolve_failed"
    assert join.calls == []  # never reached the join
    assert await fetch_readiness("acc-1", "@oops") is None


@pytest.mark.asyncio
async def test_campaign_resolve_failure_does_not_abort_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@bad")  # linked first → processed first
    await link_channel_to_campaign(campaign.campaign_id, "@good")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")

    read = _RaisingReadStub(raise_on={"@bad"})
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    result = await neurocomment.onboard_campaign(campaign.campaign_id)

    states = {o.channel: o.state for o in result.outcomes}
    assert states["@bad"] == "failed"  # resolve raise recorded, loop not aborted
    assert states["@good"] == "ready"  # the later channel still onboarded
