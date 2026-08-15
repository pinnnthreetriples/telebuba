"""Tests for neurocomment onboarding campaign behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, get_args

import pytest

from core.config import settings
from core.db import (
    assign_account_to_campaign,
    create_account,
    create_campaign,
    fetch_linked_group,
    fetch_readiness,
    link_channel_to_campaign,
)
from core.repositories.neurocomment import set_campaign_account_channels
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.neurocomment_progress import OnboardingProgressCode, OnboardingProgressEvent
from schemas.spam_status import SpamStatusVerdict
from schemas.telegram_actions import (
    ActionResult,
    BotChallengeWaitResult,
    LinkedDiscussionGroupResult,
    WaitForBotChallenge,
)
from services import neurocomment
from services.neurocomment import _seams, onboarding

if TYPE_CHECKING:
    from schemas.telegram_actions import TelegramAction, TelegramReadAction


from tests.services.neurocomment.onboarding_support import (
    _JoinStub,
    _no_sleep,
    _ReadStub,
)

pytestmark = pytest.mark.usefixtures("isolate_onboarding")

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
    await set_campaign_account_channels(campaign.campaign_id, "pinned", ["@one"])

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


class _AccountRaisingReadStub:
    """Read stub that RAISES on resolve for designated accounts (dead/banned session)."""

    def __init__(self, *, raise_for: set[str], linked_chat_id: int = 1) -> None:
        self.raise_for = raise_for
        self.result = LinkedDiscussionGroupResult(
            linked_chat_id=linked_chat_id,
            comments_enabled=True,
        )
        self.calls: list[tuple[str, TelegramReadAction]] = []

    async def execute_read(self, account_id: str, action: TelegramReadAction) -> object:
        self.calls.append((account_id, action))
        if isinstance(action, WaitForBotChallenge):
            return BotChallengeWaitResult(message=None)
        if account_id in self.raise_for:
            msg = f"dead session {account_id}"
            raise RuntimeError(msg)
        return self.result


@pytest.mark.asyncio
async def test_campaign_resolve_falls_back_to_healthy_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead first-in-order session must not block the healthy accounts behind it."""
    for acc in ("dead", "healthy"):
        await create_account(AccountCreate(account_id=acc, label=acc, session_name=acc))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await assign_account_to_campaign(campaign.campaign_id, "dead")  # first in order
    await assign_account_to_campaign(campaign.campaign_id, "healthy")

    read = _AccountRaisingReadStub(raise_for={"dead"}, linked_chat_id=77)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    result = await neurocomment.onboard_campaign(campaign.campaign_id)

    states = {o.account_id: o.state for o in result.outcomes}
    # Resolution fell through the dead session to the healthy one → onboarded, not both-failed.
    assert states["healthy"] == "ready"
    assert all(o.reason != "resolve_failed" for o in result.outcomes)
    cached = await fetch_linked_group("@chan")
    assert cached is not None
    assert cached.linked_chat_id == 77


@pytest.mark.asyncio
async def test_campaign_resolve_all_accounts_fail_marks_all_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only when EVERY account fails to resolve is the failed outcome recorded for all."""
    for acc in ("acc-1", "acc-2"):
        await create_account(AccountCreate(account_id=acc, label=acc, session_name=acc))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await assign_account_to_campaign(campaign.campaign_id, "acc-2")

    read = _AccountRaisingReadStub(raise_for={"acc-1", "acc-2"})
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    result = await neurocomment.onboard_campaign(campaign.campaign_id)

    states = {o.account_id: o.state for o in result.outcomes}
    assert states == {"acc-1": "failed", "acc-2": "failed"}
    assert all(o.reason == "resolve_failed" for o in result.outcomes)
    assert join.calls == []  # never reached a join


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


@pytest.mark.parametrize(
    ("cached_stamp", "sleeps_before_each_probe", "total_sleeps"),
    # None = a real probe, stamped now; a stamp = the older cached verdict of a TTL hit.
    [(None, [0, 1], 2), ("2026-01-01T00:00:00", [0, 0], 1)],
)
@pytest.mark.asyncio
async def test_campaign_spaces_real_spam_probes_only(
    monkeypatch: pytest.MonkeyPatch,
    cached_stamp: str | None,
    sleeps_before_each_probe: list[int],
    total_sleeps: int,
) -> None:
    """A real @SpamBot probe pauses before the next account; a TTL cache hit must not.

    The per-probe assert is the sleep count observed *at* each probe, so it stays
    independent of how many join sleeps follow. The total pins the rest: 1 join pause
    (2 accounts, 1 channel) plus the spam pauses — so a pause after the LAST probe, which
    nothing follows, would show up here as one sleep too many.
    """
    for acc in ("acc-1", "acc-2"):
        await create_account(AccountCreate(account_id=acc, label=acc, session_name=acc))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@one")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await assign_account_to_campaign(campaign.campaign_id, "acc-2")

    sleeps: list[float] = []
    seen: list[int] = []

    async def _probe(account_id: str, **_kwargs: object) -> SpamStatusVerdict:
        seen.append(len(sleeps))
        checked_at = cached_stamp or datetime.now(UTC).isoformat()
        return SpamStatusVerdict(account_id=account_id, status="clean", checked_at=checked_at)

    monkeypatch.setattr(_seams, "refresh_spam_status", _probe)
    read = _ReadStub(linked_chat_id=500, comments_enabled=True)
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", _JoinStub().execute)
    monkeypatch.setattr(_seams.rng, "uniform", lambda _a, _b: 9.0)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep(sleeps))

    await neurocomment.onboard_campaign(campaign.campaign_id)

    assert seen == sleeps_before_each_probe
    assert len(sleeps) == total_sleeps


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

    progress_events: list[OnboardingProgressEvent] = []

    def on_progress(event: OnboardingProgressEvent) -> None:
        progress_events.append(event)

    read = _ReadStub(linked_chat_id=500, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    await neurocomment.onboard_campaign(campaign.campaign_id, on_progress=on_progress)

    codes = {e.code for e in progress_events}
    assert {
        "onboarding_started",
        "spam_probe_started",
        "channel_resolving",
        "pair_joining",
        "pair_result",
        "onboarding_finished",
    } <= codes
    started = next(e for e in progress_events if e.code == "onboarding_started")
    assert started.account_count == 1
    assert started.channel_count == 1
    result = next(e for e in progress_events if e.code == "pair_result")
    assert result.account_id == "acc-1"
    assert result.state is not None
    finished = next(e for e in progress_events if e.code == "onboarding_finished")
    assert (finished.ready_count, finished.total_count) == (1, 1)


@pytest.mark.asyncio
async def test_progress_events_are_locale_neutral(monkeypatch: pytest.MonkeyPatch) -> None:
    """#12 guard: onboarding emits structured codes, never pre-translated human text."""
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")

    events: list[OnboardingProgressEvent] = []
    read = _ReadStub(linked_chat_id=500, comments_enabled=True)
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", _JoinStub().execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    await neurocomment.onboard_campaign(campaign.campaign_id, on_progress=events.append)

    valid = set(get_args(OnboardingProgressCode))
    assert events
    assert all(isinstance(e, OnboardingProgressEvent) for e in events)
    assert all(e.code in valid for e in events)
