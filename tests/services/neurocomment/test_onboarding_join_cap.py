"""Onboarding join-cap tests — the rolling-24h per-account channel-join gate."""

from __future__ import annotations

import pytest

from core.config import settings
from core.db import (
    assign_account_to_campaign,
    count_account_joins_since,
    create_account,
    create_campaign,
    link_channel_to_campaign,
    record_join,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from services import neurocomment
from services.neurocomment import _seams, onboarding
from tests.services.neurocomment.onboarding_support import _JoinStub, _no_sleep, _ReadStub

pytestmark = pytest.mark.usefixtures("isolate_onboarding")

_EPOCH = "1970-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_account_at_cap_is_skipped_without_join_or_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An account at its daily join cap performs no join RPC and no jitter sleep."""
    monkeypatch.setattr(settings.neurocomment, "max_joins_per_account_per_day", 1)
    for acc in ("acc-1", "acc-2"):
        await create_account(AccountCreate(account_id=acc, label=acc, session_name=acc))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await assign_account_to_campaign(campaign.campaign_id, "acc-2")
    # acc-1 is fresh and joins first; acc-2 already used up its one allowed join today.
    await record_join("acc-2")

    read = _ReadStub(linked_chat_id=500, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    sleeps: list[float] = []
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep(sleeps))

    result = await neurocomment.onboard_campaign(campaign.campaign_id)

    # acc-2 never sent a join RPC; only acc-1 joined.
    assert [acc for acc, _ in join.calls] == ["acc-1"]
    # The capped pair is a non-terminal "joining" (retry-later) outcome, not "ready".
    states = {o.account_id: (o.state, o.reason) for o in result.outcomes}
    assert states["acc-2"] == ("joining", "daily_join_cap")
    assert states["acc-1"][0] == "ready"
    # acc-1's real join happened first (joined_once=True), so a NON-capped acc-2 would
    # have paced a jitter pause before its join. The cap skip must avoid that pause too.
    assert sleeps == []


@pytest.mark.asyncio
async def test_successful_join_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ok join stamps the join log so the cap sees it on the next run."""
    monkeypatch.setattr(settings.neurocomment, "max_joins_per_account_per_day", 20)
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")

    read = _ReadStub(linked_chat_id=500, comments_enabled=True)
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", _JoinStub().execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    await neurocomment.onboard_campaign(campaign.campaign_id)

    assert await count_account_joins_since("acc-1", _EPOCH) == 1


@pytest.mark.asyncio
async def test_cap_zero_disables_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cap of 0 means unlimited: an account with prior joins still onboards."""
    monkeypatch.setattr(settings.neurocomment, "max_joins_per_account_per_day", 0)
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    # Far above any real cap — yet cap==0 disables the gate entirely.
    for _ in range(100):
        await record_join("acc-1")

    read = _ReadStub(linked_chat_id=500, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    result = await neurocomment.onboard_campaign(campaign.campaign_id)

    assert [acc for acc, _ in join.calls] == ["acc-1"]
    assert [o.state for o in result.outcomes] == ["ready"]


@pytest.mark.asyncio
async def test_failed_join_is_not_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only an ok join is stamped — a flood/failed RPC must not consume cap budget."""
    monkeypatch.setattr(settings.neurocomment, "max_joins_per_account_per_day", 20)
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")

    read = _ReadStub(linked_chat_id=500, comments_enabled=True)
    join = _JoinStub()
    join.set("@chan", status="flood_wait", flood_wait_seconds=60)
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    await neurocomment.onboard_campaign(campaign.campaign_id)

    # The join RPC was attempted but returned non-ok → nothing recorded.
    assert [acc for acc, _ in join.calls] == ["acc-1"]
    assert await count_account_joins_since("acc-1", _EPOCH) == 0


@pytest.mark.asyncio
async def test_operator_single_pair_respects_join_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """onboard_account_channel (the operator / retry_pair path) gates on the cap too."""
    monkeypatch.setattr(settings.neurocomment, "max_joins_per_account_per_day", 1)
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    await record_join("acc-1")  # already at cap

    read = _ReadStub(linked_chat_id=500, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)

    result = await onboarding.onboard_account_channel("acc-1", "@chan")

    # No join RPC fired; the pair is a non-terminal retry-later outcome.
    assert join.calls == []
    assert (result.state, result.reason) == ("joining", "daily_join_cap")
