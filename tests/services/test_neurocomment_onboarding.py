"""Tests for ``services.neurocomment.onboarding`` — campaign pre-onboarding.

Telegram I/O (``execute`` / ``execute_read``), randomness (``rng``) and the
inter-join sleep are patched at the service seam so the flow runs with no real
network, no jitter and no waiting. Mirrors ``tests/services/test_warming.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
    list_failed_for_channel,
    mark_human_skipped,
    update_solver_enabled,
    upsert_readiness,
)
from core.logging import reset_logging_for_tests, setup_logging
from core.repositories.neurocomment import set_campaign_account_channel
from schemas.accounts import AccountCreate
from schemas.challenge import BotChallengeMessage
from schemas.gemini import GeminiResult
from schemas.neurocomment import CampaignCreate
from schemas.spam_status import SpamStatusVerdict
from schemas.telegram_actions import (
    ActionResult,
    BotChallengeWaitResult,
    LinkedDiscussionGroupResult,
    WaitForBotChallenge,
)
from services import neurocomment
from services.neurocomment import _seams, _state, onboarding

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from schemas.telegram_actions import ActionStatus, TelegramAction, TelegramReadAction


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    # GeminiRequest requires a non-empty key (the solver builds one); CI has none.
    monkeypatch.setattr(settings.gemini, "api_key", "test-key")
    reset_logging_for_tests()
    setup_logging()
    # onboard_campaign probes each account's spam once; keep it off the network.
    monkeypatch.setattr(_seams, "refresh_spam_status", _clean_spam)
    # The solver calls Gemini on a detected (non-image) challenge — keep it off the
    # network; an error verdict makes the solver give up (→ bot_challenge).
    monkeypatch.setattr(_seams, "generate_text", _gemini_error)
    # The solver is opt-in (#148, default off); enable it for the tests that assert
    # solver behaviour — the gating tests override this per case.
    monkeypatch.setattr(settings.neurocomment, "challenge_solver_enabled", True)
    _state.reset_for_tests()
    yield
    _state.reset_for_tests()
    reset_logging_for_tests()


async def _gemini_error(_request: object) -> GeminiResult:
    return GeminiResult(status="error", error="offline in tests")


class _ReadStub:
    """Canned reads: a linked-group result for resolve, a wait result for the solver."""

    def __init__(
        self,
        *,
        linked_chat_id: int | None,
        comments_enabled: bool,
        challenge: BotChallengeMessage | None = None,
    ) -> None:
        self.result = LinkedDiscussionGroupResult(
            linked_chat_id=linked_chat_id,
            comments_enabled=comments_enabled,
        )
        self.challenge = challenge
        self.calls: list[tuple[str, TelegramReadAction]] = []

    async def execute_read(self, account_id: str, action: TelegramReadAction) -> object:
        self.calls.append((account_id, action))
        if isinstance(action, WaitForBotChallenge):
            return BotChallengeWaitResult(message=self.challenge)
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


async def _clean_spam(account_id: str, **_kwargs: object) -> SpamStatusVerdict:
    return SpamStatusVerdict(
        account_id=account_id, status="clean", checked_at="2026-01-01T00:00:00"
    )


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
async def test_campaign_pinned_account_only_onboards_its_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pinned account joins ONLY its channel; an unpinned peer joins every channel."""
    for acc in ("pinned", "free"):
        await create_account(AccountCreate(account_id=acc, label=acc, session_name=acc))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@one")
    await link_channel_to_campaign(campaign.campaign_id, "@two")
    await assign_account_to_campaign(campaign.campaign_id, "pinned")
    await assign_account_to_campaign(campaign.campaign_id, "free")
    await set_campaign_account_channel(campaign.campaign_id, "pinned", "@one")

    read = _ReadStub(linked_chat_id=500, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    await neurocomment.onboard_campaign(campaign.campaign_id)

    # The pinned account never touched @two; the free account onboarded both.
    joined = {(acc, getattr(action, "channel", "")) for acc, action in join.calls}
    assert ("pinned", "@two") not in joined
    assert ("pinned", "@one") in joined
    assert {("free", "@one"), ("free", "@two")} <= joined
    assert await fetch_readiness("pinned", "@two") is None


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


@pytest.mark.asyncio
async def test_campaign_probes_spam_once_per_account(monkeypatch: pytest.MonkeyPatch) -> None:
    for acc in ("acc-1", "acc-2"):
        await create_account(AccountCreate(account_id=acc, label=acc, session_name=acc))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@one")
    await link_channel_to_campaign(campaign.campaign_id, "@two")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await assign_account_to_campaign(campaign.campaign_id, "acc-2")

    probed: list[str] = []

    async def _record(account_id: str, **_kwargs: object) -> SpamStatusVerdict:
        probed.append(account_id)
        return SpamStatusVerdict(
            account_id=account_id, status="clean", checked_at="2026-01-01T00:00:00"
        )

    monkeypatch.setattr(_seams, "refresh_spam_status", _record)
    read = _ReadStub(linked_chat_id=500, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(_seams.rng, "uniform", lambda _a, _b: 0.0)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    await neurocomment.onboard_campaign(campaign.campaign_id)

    # Once per serving account, not once per (account, channel) pair (2 accts x 2 chans).
    assert sorted(probed) == ["acc-1", "acc-2"]


@pytest.mark.asyncio
async def test_campaign_spam_probe_failure_does_not_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")

    async def _boom(_account_id: str, **_kwargs: object) -> object:
        msg = "spambot unreachable"
        raise RuntimeError(msg)

    monkeypatch.setattr(_seams, "refresh_spam_status", _boom)
    read = _ReadStub(linked_chat_id=1, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    result = await neurocomment.onboard_campaign(campaign.campaign_id)

    # A spam-probe failure is logged, never fatal — onboarding still joins.
    assert [o.state for o in result.outcomes] == ["ready"]


@pytest.mark.asyncio
async def test_campaign_onboarding_progress_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")

    progress_messages: list[str] = []

    def on_progress(msg: str) -> None:
        progress_messages.append(msg)

    read = _ReadStub(linked_chat_id=500, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    await neurocomment.onboard_campaign(campaign.campaign_id, on_progress=on_progress)

    assert len(progress_messages) > 0
    assert any("Запуск онбординга" in msg for msg in progress_messages)
    assert any("Проверка спам-статуса" in msg for msg in progress_messages)
    assert any("Разрешение группы обсуждения" in msg for msg in progress_messages)
    assert any("вступление в группу" in msg for msg in progress_messages)
    assert any("Результат для acc-1" in msg for msg in progress_messages)
    assert any("Онбординг завершен" in msg for msg in progress_messages)


@pytest.mark.asyncio
async def test_campaign_skips_resolve_read_when_all_pairs_already_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug 3: a fully-ready channel costs ZERO Telegram reads — no resolve, no join."""
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    await create_account(AccountCreate(account_id="acc-2", label="B", session_name="acc-2"))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await assign_account_to_campaign(campaign.campaign_id, "acc-2")
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)
    await upsert_readiness("acc-2", "@chan", joined=True, captcha_passed=True, ready=True)

    read = _ReadStub(linked_chat_id=500, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    result = await neurocomment.onboard_campaign(campaign.campaign_id)

    # No resolve, no join — a fully-ready channel reads nothing from Telegram.
    assert read.calls == []
    assert join.calls == []
    states = {(o.account_id, o.state) for o in result.outcomes}
    assert states == {("acc-1", "ready"), ("acc-2", "ready")}


@pytest.mark.asyncio
async def test_human_skipped_pair_is_not_re_enabled_by_onboarding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Ф2 #148: an operator skip must survive a Start/onboard cycle — the pair must not
    # be re-joined nor have its readiness flipped back to ready.
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    # The pair was onboarded, then the operator skipped it.
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)
    await mark_human_skipped("acc-1", "@chan")

    read = _ReadStub(linked_chat_id=4423, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    await neurocomment.onboard_campaign(campaign.campaign_id)

    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    assert readiness.human_skipped is True
    assert readiness.ready is False  # NOT re-enabled
    assert all(account_id != "acc-1" for account_id, _ in join.calls)  # never re-joined


@pytest.mark.asyncio
async def test_resolve_failure_does_not_clobber_already_ready_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug 3: a transient resolve failure must not flip already-ready pairs to failed."""
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    await create_account(AccountCreate(account_id="acc-2", label="B", session_name="acc-2"))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await assign_account_to_campaign(campaign.campaign_id, "acc-2")
    # acc-1 is already ready; acc-2 is not.
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)

    # Stub _safe_resolve to return None (transient resolve failure).
    async def _none(_account_id: str, _channel: str) -> None:
        return None

    monkeypatch.setattr(onboarding, "_safe_resolve", _none)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    result = await neurocomment.onboard_campaign(campaign.campaign_id)

    states = {o.account_id: o.state for o in result.outcomes}
    # acc-1 must remain ready (NOT failed) — the resolve failure only affects acc-2.
    assert states["acc-1"] == "ready"
    assert states["acc-2"] == "failed"
    # No join calls for acc-1 (it was already ready).
    assert all(account_id != "acc-1" for account_id, _ in join.calls)


@pytest.mark.asyncio
async def test_campaign_skips_pair_only_when_captcha_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug 10: skip predicate requires captcha_passed=True.

    A readiness row with ready=True/joined=True but captcha_passed=False must
    NOT skip — the pair is re-joined. Documents the intent that a solver toggle
    does not magically re-validate previously-unchecked pairs.
    """
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    # Mark joined + ready but NOT captcha_passed — predicate must NOT treat as ready.
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=False, ready=True)

    read = _ReadStub(linked_chat_id=500, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    await neurocomment.onboard_campaign(campaign.campaign_id)

    # Pair re-joined because captcha_passed was False, NOT skipped as ready.
    assert len(join.calls) == 1
    assert join.calls[0][0] == "acc-1"


@pytest.mark.asyncio
async def test_campaign_skips_join_for_already_ready_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A re-run of onboarding does no Telegram joins / sleeps for ready pairs.

    Closes the failing flow where pressing Start (which now re-runs onboarding)
    would otherwise burn minutes re-joining accounts that were already prepared.
    """
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    # Mark the pair already ready (the state a prior onboarding would leave).
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)

    read = _ReadStub(linked_chat_id=500, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    sleeps: list[float] = []
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep(sleeps))

    result = await neurocomment.onboard_campaign(campaign.campaign_id)

    # No JoinDiscussionGroup call, no inter-pair jitter sleep, but the outcome
    # still records the pair as ready.
    assert join.calls == []
    assert sleeps == []
    assert [(o.account_id, o.state) for o in result.outcomes] == [("acc-1", "ready")]
