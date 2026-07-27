"""Onboarding tests for pairs a re-run must leave alone.

Split out of ``test_onboarding_campaign`` (file-size cap). Covers the three reasons
``onboard_campaign`` skips a pair — already ready, operator-skipped (#148), auto-banned
(#30) — plus the two ways that skip must not misfire: it needs ``captcha_passed``, and a
transient resolve failure must not clobber the pairs it doesn't concern.
"""

from __future__ import annotations

import pytest

from core.db import (
    assign_account_to_campaign,
    create_account,
    create_campaign,
    fetch_readiness,
    link_channel_to_campaign,
    mark_human_skipped,
    mark_pair_banned,
    upsert_readiness,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from services import neurocomment
from services.neurocomment import _seams, onboarding
from tests.services.neurocomment.onboarding_support import (
    _JoinStub,
    _no_sleep,
    _ReadStub,
)

pytestmark = pytest.mark.usefixtures("isolate_onboarding")


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
