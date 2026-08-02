"""The approval-gated join request: 48h from the FIRST request, two requests inside it.

Operator's rule, verbatim: «48 часов, если заявка не принимается, канал удаляем; за эти
48 часов 2 заявки». So the wall clock anchors to the first request and never restarts —
the two requests are the pacing *inside* that window, not two 48h windows. Re-sending used
to overwrite ``join_requested_at``, which pushed the deadline out to 72h. Own module
because ``test_runtime_sweep`` is already 615 lines against the 700-line test cap.
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
    fetch_readiness,
    link_channel_to_campaign,
    list_campaign_channels,
    stamp_join_request,
    upsert_readiness,
)
from core.repositories.neurocomment import set_campaign_account_channels
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from services.neurocomment import _runtime, _sweep

pytestmark = pytest.mark.usefixtures("isolate_runtime")

_CHANNEL = "@gated"


async def _pending_campaign(*accounts: str) -> str:
    """Active campaign on @gated where every account has one outstanding join request."""
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, _CHANNEL)
    for account_id in accounts:
        await create_account(AccountCreate(account_id=account_id, session_name=account_id))
        # Assigned, so the give-up rule can see who actually serves the channel.
        await assign_account_to_campaign(campaign.campaign_id, account_id)
        await upsert_readiness(
            account_id, _CHANNEL, joined=False, captcha_passed=False, ready=False
        )
        await stamp_join_request(account_id, _CHANNEL)
    return campaign.campaign_id


def _backdate_first_request(account_id: str, hours: float) -> str:
    """Push the pair's first-request anchor ``hours`` into the past; returns the stamp.

    ``stamp_join_request`` always writes real wall-clock time, so a timeline with a gap
    between the two requests can only be set up by moving the first one back.
    """
    stamp = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_readiness SET join_requested_at = ? "
            "WHERE account_id = ? AND channel = ?",
            (stamp, account_id, _CHANNEL),
        )
    return stamp


async def _gated_is_active(campaign_id: str) -> bool:
    links = (await list_campaign_channels(campaign_id)).links
    return any(link.channel == _CHANNEL and link.active for link in links)


@pytest.mark.asyncio
async def test_a_re_sent_request_does_not_restart_the_give_up_clock() -> None:
    """48h from the first request, however many of the two have gone out since."""
    campaign_id = await _pending_campaign("acc-1")
    first = _backdate_first_request("acc-1", 30)  # attempt 1, thirty hours ago
    await stamp_join_request("acc-1", _CHANNEL)  # attempt 2, sent right now

    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert (row.join_requested_at, row.join_request_attempts) == (first, 2)

    # 49h after the FIRST request: the budget is spent, whatever the second one restarted.
    await _sweep._review_join_requests(datetime.now(UTC) + timedelta(hours=19))

    assert await _gated_is_active(campaign_id) is False


@pytest.mark.asyncio
async def test_a_spent_attempt_still_waits_out_its_own_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The next request is due at ``first + attempts x retry_hours``, not at ``first + one``.

    With the anchor pinned, comparing the age against a bare retry window would authorize
    the next request the instant the FIRST window lapsed — however many had gone out in
    the meantime. Visible only above two attempts, which is why the cap is raised here.
    """
    monkeypatch.setattr(settings.neurocomment, "join_request_max_attempts", 3)
    await _pending_campaign("acc-1")
    _backdate_first_request("acc-1", 30)  # attempt 1
    await stamp_join_request("acc-1", _CHANNEL)  # attempt 2, sent 30h after it
    triggered: list[object] = []
    monkeypatch.setattr(_runtime, "_ensure_onboarding_running", triggered.append)

    now = datetime.now(UTC)
    await _sweep._review_join_requests(now + timedelta(hours=17))  # first + 47h
    assert triggered == []

    await _sweep._review_join_requests(now + timedelta(hours=19))  # first + 49h
    assert len(triggered) == 1


@pytest.mark.asyncio
async def test_a_ready_row_from_a_pinned_away_account_does_not_keep_the_channel() -> None:
    """The keep-check resolves serving accounts, exactly like the drop it guards.

    Reading every readiness row on the channel instead let a pair that is no longer this
    channel's business — its account pinned elsewhere since, or dropped from the campaign
    — vouch for a channel nobody serving it had ever been approved into, and the give-up
    rule could then never fire.
    """
    campaign_id = await _pending_campaign("acc-1")
    await create_account(AccountCreate(account_id="acc-2", session_name="acc-2"))
    await assign_account_to_campaign(campaign_id, "acc-2")
    await link_channel_to_campaign(campaign_id, "@other")
    await upsert_readiness("acc-2", _CHANNEL, joined=True, captcha_passed=True, ready=True)
    await set_campaign_account_channels(campaign_id, "acc-2", ["@other"])  # pinned away

    await _sweep._review_join_requests(datetime.now(UTC) + timedelta(hours=49))

    assert await _gated_is_active(campaign_id) is False
