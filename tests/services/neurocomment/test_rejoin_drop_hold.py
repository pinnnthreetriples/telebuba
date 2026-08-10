"""The re-join drop must leave a sibling's admin-approval window standing.

``_rejoin`` unlinks a channel once every serving pair has run out of re-joins, and it judged
that on the PARKED rows alone: a pair whose join request is waiting for an admin is neither
parked nor ready, so it neither held the drop nor kept the channel. Onboarding reaches a
fleet slowly — the rolling join cap and the join jitter — so an account that goes "awaiting
approval" a day after its siblings were kicked is entirely ordinary, and the drop annulled
the 48h of patience ``_sweep._review_join_requests`` had just started, with the admin's
Approve still to land. ``_channel_pause`` already holds its verdict for exactly this; these
tests pin the same hold on the re-join rule — and its END, because a request nobody ever
answers must not keep a finished channel linked forever.

Own module: ``test_rejoin`` sits on the 700-line test cap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    assign_account_to_campaign,
    create_account,
    create_campaign,
    link_channel_to_campaign,
    list_campaign_channels,
    stamp_join_request,
    stamp_rejoin_attempt,
    upsert_readiness,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from services.neurocomment import _rejoin, _runtime, _seams
from tests.services.neurocomment.onboarding_support import _JoinStub, _ReadStub

pytestmark = pytest.mark.usefixtures("isolate_onboarding")

_CHANNEL = "@chan"


async def _campaign(*accounts: str) -> str:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, _CHANNEL)
    for account_id in accounts:
        await create_account(AccountCreate(account_id=account_id, session_name=account_id))
        await assign_account_to_campaign(campaign.campaign_id, account_id)
    return campaign.campaign_id


async def _spend_the_budget(account_id: str) -> None:
    """Park the pair with every re-join spent and answered, its last window elapsed.

    Each stamp is followed by the re-park a failed re-join writes: that later readiness
    write is what tells the rule the attempt was answered rather than still owed.
    """
    await upsert_readiness(account_id, _CHANNEL, joined=False, captcha_passed=True, ready=False)
    for _ in range(settings.neurocomment.channel_max_rounds):
        await stamp_rejoin_attempt(account_id, _CHANNEL)
        await upsert_readiness(account_id, _CHANNEL, joined=False, captcha_passed=True, ready=False)
    _backdate("rejoin_attempted_at", account_id, hours=25)


async def _request_approval(account_id: str, *, attempts: int = 1) -> None:
    """The row ``_classify`` writes on ``InviteRequestSentError`` — asked, not refused."""
    await upsert_readiness(account_id, _CHANNEL, joined=False, captcha_passed=False, ready=False)
    for _ in range(attempts):
        await stamp_join_request(account_id, _CHANNEL)


def _backdate(column: str, account_id: str, *, hours: float) -> None:
    """Age one of the pair's stamps: both timelines here are read off the row, not off ``now``."""
    stamp = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            f"UPDATE neurocomment_readiness SET {column} = ? "  # noqa: S608 - two literal callers
            "WHERE account_id = ? AND channel = ?",
            (stamp, account_id, _CHANNEL),
        )


async def _channel_is_active(campaign_id: str) -> bool:
    links = (await list_campaign_channels(campaign_id)).links
    return any(link.channel == _CHANNEL and link.active for link in links)


def _patch_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    read = _ReadStub(linked_chat_id=4423, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(_runtime, "_ensure_onboarding_running", lambda *_args: None)


@pytest.mark.asyncio
async def test_a_pending_approval_holds_the_drop(monkeypatch: pytest.MonkeyPatch) -> None:
    """acc-2 asked ten minutes ago and its 48h has just started; acc-1 is out of re-joins.

    The shape production actually holds: access-lost pairs beside a pair with a live join
    request. Coverage is complete (the request leaves a readiness row), nothing is parked
    but acc-1 and nothing is ready — so the drop fired and the admin's Approve had nowhere
    left to land.
    """
    campaign_id = await _campaign("acc-1", "acc-2")
    await _spend_the_budget("acc-1")
    await _request_approval("acc-2")
    _patch_telegram(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert await _channel_is_active(campaign_id) is True


@pytest.mark.asyncio
async def test_an_approval_nobody_answered_stops_holding_the_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hold has to end, or a dead request keeps a finished channel linked forever.

    Both requests sent and the whole 48h of patience gone: ``_sweep._review_join_requests``
    is no longer working on the pair — it drops the channel on that same deadline — so the
    re-join rule must not sit on a verdict every serving account has earned.
    """
    campaign_id = await _campaign("acc-1", "acc-2")
    await _spend_the_budget("acc-1")
    await _request_approval("acc-2", attempts=settings.neurocomment.join_request_max_attempts)
    _backdate("join_requested_at", "acc-2", hours=49)
    _patch_telegram(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert await _channel_is_active(campaign_id) is False


@pytest.mark.asyncio
async def test_a_channel_every_account_gave_up_on_still_goes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary drop, unchanged: no request in sight, every serving pair finished."""
    campaign_id = await _campaign("acc-1", "acc-2")
    await _spend_the_budget("acc-1")
    await _spend_the_budget("acc-2")
    _patch_telegram(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert await _channel_is_active(campaign_id) is False
