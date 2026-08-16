"""Onboarding join-cap tests — the rolling-24h per-account channel-join gate."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

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
from core.repositories.neuroshilling import create_campaign as create_neuroshilling_campaign
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.neuroshilling import NeuroshillingCampaignCreate
from schemas.telegram_actions import ActionResult
from services import neurocomment
from services.neurocomment import _seams, onboarding
from services.neuroshilling import _seams as neuroshilling_seams
from services.neuroshilling import _telegram
from tests.services.neurocomment.onboarding_support import _JoinStub, _no_sleep, _ReadStub

if TYPE_CHECKING:
    from schemas.telegram_actions import TelegramAction

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
async def test_already_participant_join_is_ready_but_not_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An already-participant no-op re-join marks the pair ready without consuming cap.

    On a re-onboard the account is already in the group, so the join RPC returns
    ``already_participant``. That still yields a comment-able pair, but recording it
    would inflate the rolling-24h cap with joins that never actually happened.
    """
    monkeypatch.setattr(settings.neurocomment, "max_joins_per_account_per_day", 20)
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")

    read = _ReadStub(linked_chat_id=500, comments_enabled=True)
    join = _JoinStub()
    join.set("@chan", status="already_participant")
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))

    result = await neurocomment.onboard_campaign(campaign.campaign_id)

    assert [o.state for o in result.outcomes] == ["ready"]
    assert await count_account_joins_since("acc-1", _EPOCH) == 0


@pytest.mark.asyncio
async def test_operator_single_pair_respects_join_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """onboard_account_channel (the single-pair path) gates on the cap too."""
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


@pytest.mark.asyncio
async def test_onboarding_cannot_spend_a_slot_a_running_campaign_is_taking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One log, one budget — so the mutex over it has to be one too.

    Nothing else keeps these two apart: onboarding reads no ownership registry, the
    ``busy_neuroshilling`` gate belongs to the engine's per-post selection, and onboarding
    goes through no pacer at all — it spaces its joins with a plain jitter sleep, so the
    campaign's join queue is not one it ever waits in. The campaign is deliberately still
    inside its join RPC when onboarding reads the count — the window that let both of them
    pass a cap of one and charge it twice.
    """
    monkeypatch.setattr(settings.neurocomment, "max_joins_per_account_per_day", 1)
    monkeypatch.setattr(settings.neuroshilling, "max_joins_per_account_per_day", 1)
    # 0 disables the pacer outright, so the campaign's join is held up by nothing but
    # its own RPC — and that is what the assertions are about.
    monkeypatch.setattr(_telegram, "_join_gap_seconds", lambda: 0.0)
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    campaign = await create_neuroshilling_campaign(NeuroshillingCampaignCreate(name="Promo"))
    dispatched = asyncio.Event()

    async def campaign_join(account_id: str, action: TelegramAction) -> ActionResult:
        dispatched.set()
        # Long enough to still be in flight while onboarding does its handful of reads
        # and reaches the mutex, and no charge has landed for it yet.
        await asyncio.sleep(0.3)
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    read = _ReadStub(linked_chat_id=500, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(neuroshilling_seams, "execute", campaign_join)

    playing = asyncio.create_task(_telegram.join_target(campaign.campaign_id, "acc-1", "@target"))
    await dispatched.wait()
    onboarded = await onboarding.onboard_account_channel("acc-1", "@chan")

    assert await playing == "joined"
    assert await count_account_joins_since("acc-1", _EPOCH) == 1
    # Refused on the re-read inside the mutex: the check onboarding made before it saw
    # a budget the campaign had not spent yet.
    assert (onboarded.state, onboarded.reason) == ("joining", "daily_join_cap")
    assert join.calls == []
