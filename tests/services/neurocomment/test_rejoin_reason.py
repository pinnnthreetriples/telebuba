"""Why a pair is out of the chat — and what the re-join rule does with the answer.

One sentinel ``(joined=0, captcha_passed=1, ready=0)`` is written both by a kick and by a
join Telegram refused outright, so the rule spent the same four days on both and the board
badged both «Возвращаемся в чат». Migration #44 records the Telegram verdict beside the
sentinel: a dead address now stops costing four days and stops promising a recovery that
cannot happen, while everything else — a kick, an unmapped error, a legacy NULL — behaves
exactly as before. Own module because ``test_rejoin`` is at 491 lines against the 700-line
test cap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import (
    assign_account_to_campaign,
    create_account,
    create_campaign,
    deactivate_channel,
    fetch_readiness,
    link_channel_to_campaign,
    list_campaign_channels,
    list_recent_logs,
    stamp_rejoin_attempt,
    upsert_readiness,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.telegram_actions import ActionResult, NewPostEvent
from services import neurocomment
from services.neurocomment import _outcomes, _rejoin, _runtime, _seams, onboarding
from services.neurocomment.board import load_neurocomment_board
from tests.services.neurocomment.onboarding_support import (
    _JoinStub,
    _no_sleep,
    _ReadStub,
)

pytestmark = pytest.mark.usefixtures("isolate_onboarding")

_CHANNEL = "@chan"
# A username nobody owns: the one join verdict this suite leans on as terminal.
_DEAD_HANDLE = "UsernameNotOccupiedError"


async def _campaign(*accounts: str) -> str:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, _CHANNEL)
    for account_id in accounts:
        await create_account(AccountCreate(account_id=account_id, session_name=account_id))
        await assign_account_to_campaign(campaign.campaign_id, account_id)
    return campaign.campaign_id


def _pokes(monkeypatch: pytest.MonkeyPatch) -> list[object]:
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


async def _kicked(account_id: str, error_type: str = "ChannelPrivateError") -> None:
    """Park the pair the way a post-time access loss does — through the writer itself."""
    await _outcomes._classify_post(
        NewPostEvent(channel=_CHANNEL, post_id=10, text="hi"),
        account_id,
        "a comment",
        ActionResult(
            status="failed",
            action_type="comment_on_post",
            account_id=account_id,
            error_type=error_type,
        ),
    )


async def _refused_join(
    monkeypatch: pytest.MonkeyPatch,
    campaign_id: str,
    error_type: str,
) -> None:
    """Park the pair the way a join Telegram refused does — through onboarding itself."""
    join = _patch_joins(monkeypatch)
    join.set(_CHANNEL, status="failed", error_type=error_type)
    await neurocomment.onboard_campaign(campaign_id)


async def _channel_is_active(campaign_id: str) -> bool:
    links = (await list_campaign_channels(campaign_id)).links
    return any(link.channel == _CHANNEL and link.active for link in links)


async def _reason(account_id: str) -> str | None:
    row = await fetch_readiness(account_id, _CHANNEL)
    assert row is not None
    return row.access_lost_reason


async def _drop_event() -> tuple[str, object]:
    """The give-up line this channel got: its event code and its ``reason`` extra."""
    entry = next(
        entry
        for entry in await list_recent_logs(limit=50)
        if entry.event
        in ("neurocomment_channel_rejoin_exhausted", "neurocomment_channel_join_impossible")
    )
    return entry.event, entry.extra["reason"]


# --------------------------------------------------------------------------- #
# The writers: every sentinel now carries the verdict that produced it.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_kick_records_the_verdict_that_parked_the_pair() -> None:
    """The post path knows exactly what Telegram said; the row used to throw it away."""
    await _campaign("acc-1")

    await _kicked("acc-1", "UserNotParticipantError")

    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    # The sentinel is untouched — the reason travels BESIDE it, never instead of it.
    assert (row.joined, row.captcha_passed, row.ready) == (False, True, False)
    assert row.access_lost_reason == "UserNotParticipantError"


@pytest.mark.asyncio
async def test_a_refused_join_records_the_verdict_telegram_gave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = await _campaign("acc-1")

    await _refused_join(monkeypatch, campaign_id, _DEAD_HANDLE)

    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert (row.joined, row.captcha_passed, row.ready) == (False, True, False)
    assert row.access_lost_reason == _DEAD_HANDLE


@pytest.mark.asyncio
async def test_getting_back_into_the_group_forgets_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pair that is IN the chat has no access to have lost — the column must clear."""
    campaign_id = await _campaign("acc-1")
    await _kicked("acc-1")
    _patch_joins(monkeypatch)  # the default stub joins successfully

    await neurocomment.onboard_campaign(campaign_id)

    assert await _reason("acc-1") is None


@pytest.mark.asyncio
async def test_linking_the_channel_again_forgets_a_terminal_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A re-link is a fresh start, or the corrected handle is dropped on the next tick.

    The counters already reset here (#43) for exactly this reason; a terminal reason left
    behind would out-live them and unlink the channel again within five minutes.
    """
    campaign_id = await _campaign("acc-1")
    await _refused_join(monkeypatch, campaign_id, _DEAD_HANDLE)

    await deactivate_channel(campaign_id, _CHANNEL)
    await link_channel_to_campaign(campaign_id, _CHANNEL)

    assert await _reason("acc-1") is None


# --------------------------------------------------------------------------- #
# The rule: a hopeless pair must not spend four days proving it.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_dead_handle_is_never_retried_and_the_channel_goes_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No poke, no budget, no four days: the address does not exist and will not start to.

    The give-up line is its own event, because the exhausted one tells the operator every
    account used up its re-joins — which never happened here.
    """
    campaign_id = await _campaign("acc-1")
    await _refused_join(monkeypatch, campaign_id, _DEAD_HANDLE)
    triggered = _pokes(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert triggered == []
    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert row.rejoin_attempts == 0
    assert await _channel_is_active(campaign_id) is False
    assert await _drop_event() == ("neurocomment_channel_join_impossible", "join_impossible")


@pytest.mark.asyncio
async def test_a_kicked_pair_still_gets_its_whole_four_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A kick is the retryable half of the sentinel — the reason must not shorten it."""
    campaign_id = await _campaign("acc-1")
    await _kicked("acc-1")
    triggered = _pokes(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert len(triggered) == 1
    assert await _channel_is_active(campaign_id) is True


@pytest.mark.asyncio
async def test_an_unmapped_verdict_stays_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A false "hopeless" costs a live channel; a wasted retry costs one join RPC."""
    campaign_id = await _campaign("acc-1")
    await _refused_join(monkeypatch, campaign_id, "SomeErrorNobodyMappedYet")
    triggered = _pokes(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC) + timedelta(hours=25))

    assert len(triggered) == 1
    assert await _channel_is_active(campaign_id) is True


@pytest.mark.asyncio
async def test_a_legacy_row_without_a_reason_behaves_exactly_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NULL means "unknown", never "hopeless": every row upgraded from #43 reads that way.

    Four attempts over four days and the same give-up line as before — the shipped rule,
    unchanged, for every pair parked before the column existed.
    """
    campaign_id = await _campaign("acc-1")
    await upsert_readiness("acc-1", _CHANNEL, joined=False, captcha_passed=True, ready=False)
    assert await _reason("acc-1") is None
    _pokes(monkeypatch)
    now = datetime.now(UTC)

    for _ in range(settings.neurocomment.channel_max_rounds):
        await stamp_rejoin_attempt("acc-1", _CHANNEL)
        await upsert_readiness("acc-1", _CHANNEL, joined=False, captcha_passed=True, ready=False)
    await _rejoin.review_access_lost(now + timedelta(minutes=5))
    assert await _channel_is_active(campaign_id) is True  # the last re-join is still live

    await _rejoin.review_access_lost(now + timedelta(hours=25))

    assert await _channel_is_active(campaign_id) is False
    assert await _drop_event() == ("neurocomment_channel_rejoin_exhausted", "rejoin_exhausted")


@pytest.mark.asyncio
async def test_one_retryable_account_keeps_the_channel_for_its_full_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The drop needs EVERY parked pair to be finished, and a kicked one is not."""
    campaign_id = await _campaign("acc-1", "acc-2")
    await _refused_join(monkeypatch, campaign_id, _DEAD_HANDLE)
    await _kicked("acc-2")
    _pokes(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert await _channel_is_active(campaign_id) is True


# --------------------------------------------------------------------------- #
# The board: the badge stops promising a return that cannot happen.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_board_calls_a_dead_handle_a_failed_join_not_a_re_join(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = await _campaign("acc-1")

    await _refused_join(monkeypatch, campaign_id, _DEAD_HANDLE)

    board = await load_neurocomment_board(campaign_id)
    assert board is not None
    assert board.channels[0].status == "join_failed"


@pytest.mark.asyncio
async def test_the_board_still_reports_a_kicked_pair_as_coming_back() -> None:
    campaign_id = await _campaign("acc-1")

    await _kicked("acc-1")

    board = await load_neurocomment_board(campaign_id)
    assert board is not None
    assert board.channels[0].status == "rejoining"
