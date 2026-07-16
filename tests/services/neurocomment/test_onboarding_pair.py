"""Tests for neurocomment onboarding pair behavior."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.config import settings
from core.db import (
    create_account,
    create_campaign,
    fetch_linked_group,
    fetch_readiness,
    link_channel_to_campaign,
    list_failed_for_channel,
    update_solver_enabled,
)
from schemas.accounts import AccountCreate
from schemas.challenge import BotChallengeMessage
from schemas.neurocomment import CampaignCreate
from services.neurocomment import _seams, _state, onboarding
from tests.services.neurocomment.onboarding_support import (
    _JoinStub,
    _ReadStub,
)

pytestmark = pytest.mark.usefixtures("isolate_onboarding")

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
async def test_join_gate_error_maps_to_chat_restricted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A join that surfaces a Telegram write-forbidden error maps to ``chat_restricted``.

    Ф2 (#120) state split: a write-block error is a Telegram-level restriction
    (mute / ban), not a solvable guardian-bot challenge, so it lands in
    ``chat_restricted`` (the solver is never invoked on these). This pins the
    error->state MAPPING only; onboarding does not actively probe.
    """
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    read = _ReadStub(linked_chat_id=88, comments_enabled=True)
    join = _JoinStub()
    join.set("@captcha", status="failed", error_type="ChatGuestSendForbiddenError")
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)

    outcome = await onboarding.onboard_account_channel("acc-1", "@captcha")

    assert outcome.state == "chat_restricted"
    readiness = await fetch_readiness("acc-1", "@captcha")
    assert readiness is not None
    assert readiness.joined is True
    assert readiness.captcha_passed is False
    assert readiness.ready is False


@pytest.mark.asyncio
async def test_join_time_ban_is_sticky(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ban surfaced at join time is a sticky ban (#30), not a retryable chat_restricted."""
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    read = _ReadStub(linked_chat_id=88, comments_enabled=True)
    join = _JoinStub()
    join.set("@banned", status="failed", error_type="UserBannedInChannelError")
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)

    outcome = await onboarding.onboard_account_channel("acc-1", "@banned")

    assert outcome.state == "banned"
    readiness = await fetch_readiness("acc-1", "@banned")
    assert readiness is not None
    assert readiness.banned is True  # sticky → a re-onboard won't re-join
    assert readiness.ready is False


@pytest.mark.asyncio
async def test_successful_join_without_challenge_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ф2 #145: an ok join with no challenge in the wait window → ``ready``."""
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    read = _ReadStub(linked_chat_id=77, comments_enabled=True)  # no challenge
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)

    outcome = await onboarding.onboard_account_channel("acc-1", "@chan")

    assert outcome.state == "ready"
    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    assert readiness.ready is True
    assert (await list_failed_for_channel("@chan", limit=10)).rows == []


@pytest.mark.asyncio
async def test_successful_join_with_challenge_is_bot_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ф2 #145: an ok join where the solver detects a challenge → ``bot_challenge``.

    The pair is joined but not ready; the solver's audit row is what the board
    later reads to render ``bot_challenge`` (vs the unsolvable ``chat_restricted``).
    """
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    message = BotChallengeMessage(
        text="докажи, что не бот", button_labels=["Я человек"], message_id=5, has_photo=False
    )
    read = _ReadStub(linked_chat_id=77, comments_enabled=True, challenge=message)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)

    outcome = await onboarding.onboard_account_channel("acc-1", "@chan")

    assert outcome.state == "bot_challenge"
    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    assert readiness.joined is True
    assert readiness.ready is False
    failed = await list_failed_for_channel("@chan", limit=10)
    assert len(failed.rows) == 1
    assert failed.rows[0].outcome == "give_up"


async def _campaign_with_channel(channel: str, *, solver_enabled: bool | None) -> None:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, channel)
    await update_solver_enabled(campaign.campaign_id, value=solver_enabled)


def _challenge_read() -> _ReadStub:
    message = BotChallengeMessage(
        text="докажи, что не бот", button_labels=["Я человек"], message_id=5, has_photo=False
    )
    return _ReadStub(linked_chat_id=77, comments_enabled=True, challenge=message)


@pytest.mark.asyncio
async def test_solver_runs_when_campaign_overrides_global_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ф2 #148: campaign solver_enabled=True beats a global flag of False → solver runs."""
    monkeypatch.setattr(settings.neurocomment, "challenge_solver_enabled", False)
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    await _campaign_with_channel("@chan", solver_enabled=True)
    read = _challenge_read()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", _JoinStub().execute)

    outcome = await onboarding.onboard_account_channel("acc-1", "@chan")

    assert outcome.state == "bot_challenge"  # solver ran, detected, gave up


@pytest.mark.asyncio
async def test_solver_skipped_when_campaign_overrides_global_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ф2 #148: campaign solver_enabled=False beats a global flag of True → solver skipped."""
    monkeypatch.setattr(settings.neurocomment, "challenge_solver_enabled", True)
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    await _campaign_with_channel("@chan", solver_enabled=False)
    read = _challenge_read()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", _JoinStub().execute)

    outcome = await onboarding.onboard_account_channel("acc-1", "@chan")

    assert outcome.state == "ready"  # solver never ran despite a challenge present
    assert (await list_failed_for_channel("@chan", limit=10)).rows == []


@pytest.mark.asyncio
async def test_solver_off_by_default_when_both_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ф2 #148: campaign None + global False (the defaults) → solver does not run (opt-in)."""
    monkeypatch.setattr(settings.neurocomment, "challenge_solver_enabled", False)
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    await _campaign_with_channel("@chan", solver_enabled=None)
    read = _challenge_read()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", _JoinStub().execute)

    outcome = await onboarding.onboard_account_channel("acc-1", "@chan")

    assert outcome.state == "ready"


@pytest.mark.asyncio
async def test_channel_in_challenge_backoff_skips_join(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ф2 #147: a backed-off channel is left alone — no join, no solver → bot_challenge_backoff."""
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    _state.register_challenge_failure(
        "@chan", datetime.now(UTC), min_failures=1, base_seconds=3600, max_seconds=86400
    )
    read = _ReadStub(linked_chat_id=77, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)

    outcome = await onboarding.onboard_account_channel("acc-1", "@chan")

    assert outcome.state == "bot_challenge_backoff"
    assert join.calls == []  # no join attempted


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
    # A hard failure persists a distinct signal (captcha_passed on an unjoined row)
    # so the board renders join_failed, not join_by_request. It never joined.
    assert readiness.joined is False
    assert readiness.captcha_passed is True
