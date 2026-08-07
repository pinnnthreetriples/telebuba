"""A channel whose comments are switched off leaves its campaign, from either surface.

The verdict used to be a green INFO line and nothing else: the channel stayed linked, was
re-resolved on every onboarding pass, and re-reported the same line forever, while no
readiness row was ever written for it — so none of the three sibling drop rules could
reach it either. These tests pin the two lines, their severity, and the unlink itself.
"""

from __future__ import annotations

import pytest

from core.db import (
    assign_account_to_campaign,
    create_account,
    create_campaign,
    link_channel_to_campaign,
    list_campaign_channels,
    list_recent_logs,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from services.neurocomment import _seams, bans, onboarding
from tests.services.neurocomment.onboarding_support import _JoinStub, _ReadStub

pytestmark = pytest.mark.usefixtures("isolate_onboarding")

_CHANNEL = "@silent"


async def _campaign(*accounts: str) -> str:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, _CHANNEL)
    for account_id in accounts:
        await create_account(AccountCreate(account_id=account_id, session_name=account_id))
        await assign_account_to_campaign(campaign.campaign_id, account_id)
    return campaign.campaign_id


async def _channel_is_active(campaign_id: str) -> bool:
    links = (await list_campaign_channels(campaign_id)).links
    return any(link.channel == _CHANNEL and link.active for link in links)


async def _lines() -> list[tuple[str, str]]:
    """The comments-off pair, oldest first: ``(level, event)``."""
    return [
        (entry.level, entry.event)
        for entry in reversed(await list_recent_logs(limit=50))
        if entry.event.startswith("neurocomment_channel_comments_off")
    ]


def _silent_channel(monkeypatch: pytest.MonkeyPatch) -> _JoinStub:
    read = _ReadStub(linked_chat_id=None, comments_enabled=False)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    return join


@pytest.mark.asyncio
async def test_onboarding_unlinks_the_channel_and_says_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two lines the operator asked for, in order, and the unlink under them."""
    campaign_id = await _campaign("acc-1")
    _silent_channel(monkeypatch)

    outcome = await onboarding.onboard_account_channel("acc-1", _CHANNEL)

    assert outcome.state == "comments_off"
    assert await _channel_is_active(campaign_id) is False
    assert await _lines() == [
        ("ERROR", "neurocomment_channel_comments_off"),
        ("ERROR", "neurocomment_channel_comments_off_dropped"),
    ]


@pytest.mark.asyncio
async def test_the_verdict_stops_repeating_once_the_channel_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole reason the line was noise: every pass re-read the same dead channel."""
    campaign_id = await _campaign("acc-1")
    _silent_channel(monkeypatch)

    await onboarding.onboard_campaign(campaign_id)
    await onboarding.onboard_campaign(campaign_id)

    assert await _channel_is_active(campaign_id) is False
    assert await _lines() == [
        ("ERROR", "neurocomment_channel_comments_off"),
        ("ERROR", "neurocomment_channel_comments_off_dropped"),
    ]


@pytest.mark.asyncio
async def test_a_channel_in_no_campaign_is_reported_and_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator's single-pair retry reaches channels nothing has linked — no crash."""
    await create_account(AccountCreate(account_id="acc-1", session_name="acc-1"))
    _silent_channel(monkeypatch)

    outcome = await onboarding.onboard_account_channel("acc-1", _CHANNEL)

    assert outcome.state == "comments_off"
    assert await _lines() == [("ERROR", "neurocomment_channel_comments_off")]


@pytest.mark.asyncio
async def test_the_check_channels_button_drops_it_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """Its probe reaches the same verdict by another route, and it used to shrug it off.

    ``comments_disabled`` folded into the ``unknown`` aggregate, so the button reported
    "не знаем" about a channel Telegram had just called impossible.
    """
    campaign_id = await _campaign("acc-1")
    read = _ReadStub(linked_chat_id=None, comments_enabled=False, ban_state="comments_disabled")
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)

    result = await bans.check_campaign_channel_bans(campaign_id)

    assert result is not None
    assert [(item.channel, item.status) for item in result.items] == [(_CHANNEL, "unknown")]
    assert await _channel_is_active(campaign_id) is False
    assert await _lines() == [
        ("ERROR", "neurocomment_channel_comments_off"),
        ("ERROR", "neurocomment_channel_comments_off_dropped"),
    ]


@pytest.mark.asyncio
async def test_a_group_this_account_cannot_resolve_is_not_a_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe says ``comments_disabled`` for a group it merely could not RESOLVE.

    An account that never onboarded into the chat, one just kicked out of it, or a
    FloodWait on the entity read all land there (``_read._resolve_linked_group_entity``
    → None). Dropping on that would unlink a channel the other accounts comment in fine
    — and the unlink deletes their pins, which nothing restores. So the authoritative
    read decides, and here it says comments are ON.
    """
    campaign_id = await _campaign("acc-1")
    read = _ReadStub(linked_chat_id=4423, comments_enabled=True, ban_state="comments_disabled")
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)

    result = await bans.check_campaign_channel_bans(campaign_id)

    assert result is not None
    assert [(item.channel, item.status) for item in result.items] == [(_CHANNEL, "unknown")]
    assert await _channel_is_active(campaign_id) is True
    assert await _lines() == []
