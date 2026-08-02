"""An account kicked out of a discussion group gets itself back in — 4 tries, one a day.

The parked pair carries onboarding's hard-join-failure sentinel and nothing used to retry
it: onboarding has no timer, so a kicked account waited for an operator, and a channel
whose every account was kicked produced nothing at all. The rule rides the deletion sweep
(``_rejoin.review_access_lost``), which only POKES onboarding — the joining itself stays
in the onboarding pass, behind its join cap and jitter. Own module because
``test_runtime_sweep`` is already 556 lines against the 700-line test cap.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from core.config import settings
from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    assign_account_to_campaign,
    create_account,
    create_campaign,
    fetch_readiness,
    link_channel_to_campaign,
    list_campaign_channels,
    list_recent_logs,
    mark_human_skipped,
    stamp_rejoin_attempt,
    upsert_readiness,
)
from core.repositories.neurocomment._tables import _neurocomment_readiness
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate, CampaignStatus
from services import neurocomment
from services.neurocomment import _rejoin, _runtime, _seams, onboarding
from tests.services.neurocomment.onboarding_support import (
    _JoinStub,
    _no_sleep,
    _ReadStub,
)

pytestmark = pytest.mark.usefixtures("isolate_onboarding")

_CHANNEL = "@chan"


async def _campaign(*accounts: str, status: CampaignStatus = "active") -> str:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status=status))
    await link_channel_to_campaign(campaign.campaign_id, _CHANNEL)
    for account_id in accounts:
        await create_account(AccountCreate(account_id=account_id, session_name=account_id))
        await assign_account_to_campaign(campaign.campaign_id, account_id)
    return campaign.campaign_id


async def _park(account_id: str, *, attempts: int = 0) -> None:
    """Leave the pair exactly as a post-time access loss does, with ``attempts`` spent."""
    await upsert_readiness(account_id, _CHANNEL, joined=False, captcha_passed=True, ready=False)
    for _ in range(attempts):
        await stamp_rejoin_attempt(account_id, _CHANNEL)


async def _rewind_attempts(hours: float) -> None:
    """Age every re-join stamp, standing in for the daily window elapsing."""
    when = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()

    def _write() -> None:
        with _get_engine().begin() as connection:
            connection.execute(update(_neurocomment_readiness).values(rejoin_attempted_at=when))

    await asyncio.to_thread(_write)


async def _channel_is_active(campaign_id: str) -> bool:
    links = (await list_campaign_channels(campaign_id)).links
    return any(link.channel == _CHANNEL and link.active for link in links)


def _pokes(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Capture the onboarding pokes instead of spawning a real pass."""
    triggered: list[object] = []
    monkeypatch.setattr(_runtime, "_ensure_onboarding_running", triggered.append)
    return triggered


def _patch_joins(monkeypatch: pytest.MonkeyPatch) -> _JoinStub:
    read = _ReadStub(linked_chat_id=4423, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))
    return join


# --------------------------------------------------------------------------- #
# The retry timeline: the sweep pokes onboarding, once per window, four times.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_freshly_kicked_pair_is_retried_by_the_very_next_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A never-retried pair is due immediately — a transient kick costs minutes, not a day."""
    await _campaign("acc-1")
    await _park("acc-1")
    triggered = _pokes(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert len(triggered) == 1


@pytest.mark.asyncio
async def test_a_spent_attempt_is_not_retried_before_the_window_and_is_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    campaign_id = await _campaign("acc-1")
    await _park("acc-1", attempts=1)
    triggered = _pokes(monkeypatch)

    await _rejoin.review_access_lost(now + timedelta(hours=23))
    assert triggered == []
    # Waiting out a window is not a verdict: the pair still has three attempts left.
    assert await _channel_is_active(campaign_id) is True

    await _rejoin.review_access_lost(now + timedelta(hours=25))
    assert len(triggered) == 1


@pytest.mark.asyncio
async def test_attempts_stop_at_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Four spent attempts: no fifth, however long the pair sits there."""
    await _campaign("acc-1", "acc-2")  # a second serving account, so nothing is unlinked
    await _park("acc-1", attempts=settings.neurocomment.channel_max_rounds)
    triggered = _pokes(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC) + timedelta(days=30))

    assert triggered == []


@pytest.mark.asyncio
async def test_a_skipped_pair_that_looks_parked_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator skip on an unjoined pair matches the sentinel field for field.

    Onboarding refuses to re-join it, so counting it as parked would make it due forever
    and poke onboarding on every single sweep tick.
    """
    campaign_id = await _campaign("acc-1")
    await _park("acc-1")
    await mark_human_skipped("acc-1", _CHANNEL)
    triggered = _pokes(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC) + timedelta(days=30))

    assert triggered == []
    assert await _channel_is_active(campaign_id) is True


# --------------------------------------------------------------------------- #
# The onboarding half: the pass is what joins, and it honours the same window.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_onboarding_holds_a_kicked_pair_back_inside_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator Start must not spend all four attempts in an hour."""
    campaign_id = await _campaign("acc-1")
    await _park("acc-1", attempts=1)
    join = _patch_joins(monkeypatch)

    result = await neurocomment.onboard_campaign(campaign_id)

    assert join.calls == []
    assert [(o.state, o.reason) for o in result.outcomes] == [("joining", "rejoin_backoff")]


@pytest.mark.asyncio
async def test_a_successful_re_join_clears_the_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Back in the group: the next access loss starts from attempt one, not the cap."""
    campaign_id = await _campaign("acc-1")
    await _park("acc-1", attempts=3)
    await _rewind_attempts(hours=25)
    join = _patch_joins(monkeypatch)

    result = await neurocomment.onboard_campaign(campaign_id)

    assert [account_id for account_id, _ in join.calls] == ["acc-1"]
    assert [o.state for o in result.outcomes] == ["ready"]
    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert (row.rejoin_attempts, row.rejoin_attempted_at) == (0, None)


@pytest.mark.asyncio
async def test_a_failed_re_join_spends_exactly_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stamp is paid before the RPC, so the re-parked sentinel cannot retry forever."""
    campaign_id = await _campaign("acc-1")
    await _park("acc-1")
    join = _patch_joins(monkeypatch)
    join.set(_CHANNEL, status="failed", error_type="ChannelPrivateError")

    await neurocomment.onboard_campaign(campaign_id)

    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    # Still the sentinel (so the rule keeps applying), and one attempt lighter.
    assert (row.joined, row.captcha_passed, row.ready) == (False, True, False)
    assert row.rejoin_attempts == 1
    # ...and a second pass right away spends nothing more.
    await neurocomment.onboard_campaign(campaign_id)
    assert len(join.calls) == 1


# --------------------------------------------------------------------------- #
# Give-up: the channel leaves its campaign only when nothing works there.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_last_exhausted_account_unlinks_the_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = await _campaign("acc-1", "acc-2")
    for account_id in ("acc-1", "acc-2"):
        await _park(account_id, attempts=settings.neurocomment.channel_max_rounds)
    _pokes(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert await _channel_is_active(campaign_id) is False
    dropped = next(
        entry
        for entry in await list_recent_logs(limit=50)
        if entry.event == "neurocomment_channel_rejoin_exhausted"
    )
    assert dropped.level == "WARNING"
    assert dropped.extra["channel"] == _CHANNEL
    assert dropped.extra["parked_accounts"] == 2
    assert dropped.extra["reason"] == "rejoin_exhausted"


@pytest.mark.asyncio
async def test_a_channel_with_a_ready_account_is_never_unlinked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One stubborn account must not kill a channel the others comment in fine."""
    campaign_id = await _campaign("acc-1", "acc-2")
    await _park("acc-1", attempts=settings.neurocomment.channel_max_rounds)
    await upsert_readiness("acc-2", _CHANNEL, joined=True, captcha_passed=True, ready=True)
    _pokes(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert await _channel_is_active(campaign_id) is True


@pytest.mark.asyncio
async def test_a_serving_account_with_no_readiness_row_keeps_the_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing row means that account was never tried here — not that it failed."""
    campaign_id = await _campaign("acc-1", "acc-2")
    await _park("acc-1", attempts=settings.neurocomment.channel_max_rounds)
    _pokes(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert await _channel_is_active(campaign_id) is True


@pytest.mark.asyncio
async def test_a_channel_outside_an_active_campaign_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paused campaign posts nothing, so its channels are neither retried nor dropped."""
    campaign_id = await _campaign("acc-1", status="paused")
    await _park("acc-1")
    triggered = _pokes(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert triggered == []
    assert await _channel_is_active(campaign_id) is True
