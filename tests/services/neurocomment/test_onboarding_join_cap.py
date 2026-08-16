"""Join-cap tests — the rolling-24h per-account channel-join gate.

Onboarding owns most of them; the last one is the listener's channel-join pass, which
spends the same per-account budget and is therefore gated the same way.
"""

from __future__ import annotations

import asyncio
import contextlib
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
from services.neurocomment import _join, _seams, onboarding
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


@pytest.mark.asyncio
async def test_listener_join_pass_cannot_spend_a_slot_a_running_campaign_is_taking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The listener's channel-join pass is the third charger of that one budget.

    It charges the log with the listener account, and an operator can put that account
    in a neuroshilling roster: ``_claim_accounts`` asks the neurocomment campaign
    membership and the ownership registry, and the listener is in neither. Two join
    passes then run on one account — and this one is deliberately reading the count
    while the campaign's join is still in flight, the window that let both of them pass
    a cap of one and charge it twice.

    The campaign's RPC waits on the pass's counts instead of sleeping through them, so
    which of the two charges first is settled by the mutex rather than by the machine.
    """
    monkeypatch.setattr(settings.neurocomment, "max_joins_per_account_per_day", 1)
    monkeypatch.setattr(settings.neuroshilling, "max_joins_per_account_per_day", 1)
    # 0 disables the pacer outright: the campaign's join is held up by its own RPC alone.
    monkeypatch.setattr(_telegram, "_join_gap_seconds", lambda: 0.0)
    await create_account(
        AccountCreate(account_id="listener-1", label="L", session_name="listener-1")
    )
    watched = await create_campaign(CampaignCreate(name="Watch", prompt="p"))
    await link_channel_to_campaign(watched.campaign_id, "@watched")
    playing_campaign = await create_neuroshilling_campaign(
        NeuroshillingCampaignCreate(name="Promo")
    )
    dispatched, counted, recounted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    at_join_cap = _join._at_join_cap

    async def counting_at_join_cap(account_id: str) -> bool:
        verdict = await at_join_cap(account_id)
        (recounted if counted.is_set() else counted).set()
        return verdict

    async def campaign_join(account_id: str, action: TelegramAction) -> ActionResult:
        dispatched.set()
        # In flight, and charged for by nobody, until the pass has counted the budget.
        # The timeout is a deadlock guard rather than a race window: a pass that never
        # counts would otherwise leave this waiting for ever.
        await asyncio.wait_for(counted.wait(), timeout=5.0)
        # Waiting for the pass's SECOND count as well is what decides this case without
        # a race: a pass queued on the mutex cannot reach that count while this join
        # holds it, so the wait expires and this join charges first — and a pass that is
        # NOT queued on it gets there within microseconds, ahead of that charge.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(recounted.wait(), timeout=0.1)
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    join = _JoinStub()
    monkeypatch.setattr(_join, "_at_join_cap", counting_at_join_cap)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(neuroshilling_seams, "execute", campaign_join)

    playing = asyncio.create_task(
        _telegram.join_target(playing_campaign.campaign_id, "listener-1", "@target")
    )
    await dispatched.wait()
    await _join.run_join_pass("listener-1")

    assert await playing == "joined"
    assert await count_account_joins_since("listener-1", _EPOCH) == 1
    # Refused on the count taken inside the mutex, so the watched channel is never
    # joined — the count before it read a budget the campaign had not charged against.
    assert join.calls == []
