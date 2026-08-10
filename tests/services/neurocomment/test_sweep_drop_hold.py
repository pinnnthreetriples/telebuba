"""The join-request drop must leave a sibling's re-join budget standing.

The mirror of ``test_rejoin_drop_hold``. ``_sweep._review_join_requests`` unlinks a channel
once every outstanding approval request has burned its 48h, and it judged that on coverage
and "any row ready" alone: a pair partway through ``_rejoin``'s re-join budget is neither
ready nor absent, so it counted as tried and held nothing. Onboarding reaches a fleet
slowly — the rolling join cap and the join jitter — so an account still working its way back
into the chat while a LATER account's request expires is entirely ordinary, and the drop
annulled that budget with a join in flight. ``_channel_pause`` already holds its verdict for
exactly this; these tests pin the same hold on the request rule — and its END, because a
budget that is finished must not keep a dead channel linked forever.

Own module: ``test_rejoin`` sits on the 700-line test cap and the sibling fix set the
precedent of a focused one.
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
from services.neurocomment import _sweep

pytestmark = pytest.mark.usefixtures("isolate_runtime")

_CHANNEL = "@gated"


async def _campaign(*accounts: str) -> str:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, _CHANNEL)
    for account_id in accounts:
        await create_account(AccountCreate(account_id=account_id, session_name=account_id))
        await assign_account_to_campaign(campaign.campaign_id, account_id)
    return campaign.campaign_id


async def _expired_request(account_id: str) -> None:
    """A pair whose whole 48h of patience has gone: both requests sent, nobody approved."""
    await upsert_readiness(account_id, _CHANNEL, joined=False, captcha_passed=False, ready=False)
    for _ in range(settings.neurocomment.join_request_max_attempts):
        await stamp_join_request(account_id, _CHANNEL)
    _backdate("join_requested_at", account_id, hours=49)


async def _park_access_lost(account_id: str) -> None:
    """The hard-join-failure sentinel with the whole re-join budget still to spend."""
    await upsert_readiness(account_id, _CHANNEL, joined=False, captcha_passed=True, ready=False)


async def _spend_the_budget(account_id: str) -> None:
    """Park the pair with every re-join spent and answered, its last window elapsed.

    Each stamp is followed by the re-park a failed re-join writes: that later readiness
    write is what tells the rule the attempt was answered rather than still owed.
    """
    await _park_access_lost(account_id)
    for _ in range(settings.neurocomment.channel_max_rounds):
        await stamp_rejoin_attempt(account_id, _CHANNEL)
        await _park_access_lost(account_id)
    _backdate("rejoin_attempted_at", account_id, hours=25)


def _backdate(column: str, account_id: str, *, hours: float) -> None:
    """Age one of the pair's stamps: both timelines here are read off the row, not ``now``."""
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


@pytest.mark.asyncio
async def test_a_sibling_still_rejoining_holds_the_drop() -> None:
    """acc-1's 48h is gone; acc-2 was kicked and has its whole re-join budget to spend.

    The shape production actually holds: an approval-gated pair beside a pair the re-join
    rule is still working on. Coverage is complete (the kicked pair has a readiness row)
    and nothing is ready — so the drop fired and annulled a budget with attempts in flight.
    """
    campaign_id = await _campaign("acc-1", "acc-2")
    await _expired_request("acc-1")
    await _park_access_lost("acc-2")

    await _sweep._review_join_requests(datetime.now(UTC))

    assert await _channel_is_active(campaign_id) is True


@pytest.mark.asyncio
async def test_a_sibling_out_of_rejoins_does_not_hold_the_drop() -> None:
    """The hold has to end, or a finished channel stays linked with nothing working.

    acc-2 has spent every re-join, each one answered, and the last window has run out:
    ``_rejoin`` is done with the pair — it drops the channel itself on that same deadline —
    so the request rule must not sit on a verdict every serving account has earned.
    """
    campaign_id = await _campaign("acc-1", "acc-2")
    await _expired_request("acc-1")
    await _spend_the_budget("acc-2")

    await _sweep._review_join_requests(datetime.now(UTC))

    assert await _channel_is_active(campaign_id) is False


@pytest.mark.asyncio
async def test_the_ordinary_expired_approval_drop_still_fires() -> None:
    """The ordinary drop, unchanged: nobody re-joining, every request out of patience."""
    campaign_id = await _campaign("acc-1", "acc-2")
    await _expired_request("acc-1")
    await _expired_request("acc-2")

    await _sweep._review_join_requests(datetime.now(UTC))

    assert await _channel_is_active(campaign_id) is False
