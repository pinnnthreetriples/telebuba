"""Tests for neurocomment onboarding campaign behavior."""

from __future__ import annotations

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
    mark_human_skipped,
    mark_pair_banned,
    upsert_readiness,
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
async def test_banned_pair_is_not_re_enabled_by_onboarding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # #30: an auto-ban must survive a Start/onboard cycle — the pair must not be
    # re-joined nor have its readiness flipped back to ready (or the engine would
    # keep hitting the ban).
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    # The pair was onboarded, then got banned while commenting.
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=True, ready=True)
    await mark_pair_banned("acc-1", "@chan")

    read = _ReadStub(linked_chat_id=4423, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    await neurocomment.onboard_campaign(campaign.campaign_id)

    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    assert readiness.banned is True
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
