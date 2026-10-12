"""The soft neurocomment start (Прогрев 2.0, item 2.4): its day, its caps, every gate.

Five days after a «Мягкий старт» hand-off the fleet caps are lowered to the soft ones
(days 1-2: 2/h, 1 per channel, 5 joins; days 3-5: 4 / 2 / 10) and a per-day comment cap
ramps 5 → 15. An operator's own override still applies as written.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import (
    assign_account_to_campaign,
    claim_comment,
    create_account,
    create_campaign,
    link_channel_to_campaign,
    mark_nc_handed_off,
    mark_promoted_to_nc,
    upsert_warming_state,
)
from core.repositories.warming_marks import store_nc_start
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.neurocomment_limits import AccountLimitOverride, AccountLimitsUpdate
from schemas.warming import WarmingStateRecord, WarmingStateWrite
from services._account_limits import account_join_cap, account_limits, resolve_limits
from services._soft_start import soft_caps, soft_day
from services.neurocomment import _gates, engine, limits, settings_store
from services.neurocomment.board import load_neurocomment_board

pytestmark = pytest.mark.usefixtures("isolate_onboarding")

_HANDED = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _record(**fields: object) -> WarmingStateRecord:
    base: dict[str, object] = {
        "account_id": "acc-1",
        "state": "idle",
        "updated_at": "t",
        "promoted_to_nc": True,
        "nc_handed_off": True,
        "nc_start_mode": "soft",
        "nc_handoff_at": _HANDED.isoformat(),
    }
    base.update(fields)
    return WarmingStateRecord.model_validate(base)


@pytest.mark.parametrize(
    ("elapsed", "day"),
    [
        (timedelta(0), 1),
        (timedelta(hours=23, minutes=59), 1),
        (timedelta(days=1), 2),
        (timedelta(days=4, hours=12), 5),
        (timedelta(days=5), None),
        (timedelta(days=-1), None),
    ],
)
def test_the_soft_day_counts_whole_days_from_the_hand_off(
    elapsed: timedelta, day: int | None
) -> None:
    assert soft_day(_record(), _HANDED + elapsed) == day


@pytest.mark.parametrize(
    "fields",
    [
        {"nc_start_mode": "normal"},
        {"nc_start_mode": None},
        {"nc_handed_off": False},
        {"nc_handoff_at": None},
        {"nc_handoff_at": "not-a-date"},
    ],
)
def test_no_soft_day_outside_a_soft_hand_off(fields: dict[str, object]) -> None:
    assert soft_day(_record(**fields), _HANDED) is None
    assert soft_day(None, _HANDED) is None


def test_a_naive_hand_off_stamp_reads_as_utc() -> None:
    naive = _record(nc_handoff_at="2026-06-01T12:00:00")

    assert soft_day(naive, _HANDED + timedelta(days=2)) == 3


def test_the_caps_step_up_after_the_early_days_and_the_day_cap_ramps() -> None:
    caps = [soft_caps(day) for day in range(1, 6)]

    assert [c.comments_per_day for c in caps] == [5, 8, 10, 13, 15]
    assert {
        (c.comments_per_hour, c.comments_per_channel_per_day, c.joins_per_day) for c in caps[:2]
    } == {(2, 1, 5)}
    assert {
        (c.comments_per_hour, c.comments_per_channel_per_day, c.joins_per_day) for c in caps[2:]
    } == {(4, 2, 10)}


def test_a_one_day_soft_start_holds_the_first_day_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.soft_start, "days", 1)

    assert soft_caps(1).comments_per_day == settings.soft_start.first_day_comments


@pytest.mark.asyncio
async def test_soft_lowers_the_fleet_and_turns_a_fleet_no_cap_into_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "max_joins_per_account_per_day", 0)
    fleet = (await settings_store.load_settings()).model_copy(
        update={"max_comments_per_hour": 30, "max_comments_per_channel_per_day": 0},
    )

    normal = resolve_limits(None, fleet)
    early = resolve_limits(None, fleet, 1)
    late = resolve_limits(None, fleet, 4)

    assert (normal.max_comments_per_hour, normal.max_comments_per_day) == (30, 0)
    assert (
        early.max_comments_per_hour,
        early.max_comments_per_channel_per_day,
        early.max_joins_per_day,
        early.max_comments_per_day,
    ) == (2, 1, 5, 5)
    assert (late.max_comments_per_hour, late.max_joins_per_day, late.max_comments_per_day) == (
        4,
        10,
        13,
    )


@pytest.mark.asyncio
async def test_a_fleet_already_below_the_soft_cap_stays_lower() -> None:
    fleet = (await settings_store.load_settings()).model_copy(
        update={"max_comments_per_hour": 1},
    )

    assert resolve_limits(None, fleet, 4).max_comments_per_hour == 1


@pytest.mark.asyncio
async def test_a_manual_override_still_applies_in_a_soft_start() -> None:
    fleet = await settings_store.load_settings()
    override = AccountLimitOverride(account_id="acc-1", max_comments_per_hour=9)

    caps = resolve_limits(override, fleet, 1)

    assert caps.max_comments_per_hour == 9
    assert caps.max_comments_per_day == soft_caps(1).comments_per_day


async def _soft_account(account_id: str = "acc-1", *, at: datetime | None = None) -> None:
    await create_account(
        AccountCreate(account_id=account_id, label=account_id, session_name=account_id),
    )
    await upsert_warming_state(WarmingStateWrite(account_id=account_id, state="idle"))
    await mark_promoted_to_nc(account_id)
    await mark_nc_handed_off(account_id)
    await store_nc_start(account_id, "soft", (at or datetime.now(UTC)).isoformat())


@pytest.mark.asyncio
async def test_the_join_cap_of_a_soft_account_is_the_soft_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "max_joins_per_account_per_day", 20)
    await _soft_account()
    await _soft_account("acc-old", at=datetime.now(UTC) - timedelta(days=6))

    assert await account_join_cap("acc-1", 20) == 5
    assert await account_join_cap("acc-old", 20) == 20
    await limits.save_account_limits("acc-1", AccountLimitsUpdate(max_joins_per_day=7))
    assert await account_join_cap("acc-1", 20) == 7


@pytest.mark.asyncio
async def test_the_limits_view_reports_the_soft_caps() -> None:
    await _soft_account()

    view = await limits.load_account_limits("acc-1")

    assert view is not None
    assert view.comments_per_hour.limit == 2
    assert view.joins.limit == 5


async def _campaign(account_id: str = "acc-1") -> str:
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await assign_account_to_campaign(campaign.campaign_id, account_id)
    return campaign.campaign_id


@pytest.mark.asyncio
async def test_the_soft_day_cap_refuses_once_the_day_is_spent() -> None:
    await _soft_account()
    campaign_id = await _campaign()
    # Lift the hour and per-channel caps so only the soft day cap (5 on day 1) is left.
    await limits.save_account_limits(
        "acc-1",
        AccountLimitsUpdate(max_comments_per_hour=100, max_comments_per_channel_per_day=0),
    )
    fleet = await settings_store.load_settings()
    for post_id in range(4):
        await claim_comment(f"@c{post_id}", post_id, campaign_id, "acc-1")

    assert await _gates._account_quota_block_reason("acc-1", "@chan", fleet) is None
    await claim_comment("@c9", 9, campaign_id, "acc-1")
    assert await _gates._account_quota_block_reason("acc-1", "@chan", fleet) == "quota_soft_day"
    caps = await account_limits("acc-1", fleet)
    assert caps.max_comments_per_day == 5


@pytest.mark.asyncio
async def test_the_selection_pool_reads_day_totals_only_for_a_soft_candidate() -> None:
    await _soft_account()
    campaign_id = await _campaign()
    await claim_comment("@other", 1, campaign_id, "acc-1")
    fleet = await settings_store.load_settings()
    now = datetime.now(UTC)

    pool = await engine._load_selection_pool(campaign_id, "@chan", ["acc-1"], now, fleet)

    assert pool.day_totals == {"acc-1": 1}
    caps = resolve_limits(None, fleet, 1)
    assert _gates._quota_block_reason("acc-1", caps, {}, {}, {"acc-1": 5}) == "quota_soft_day"
    assert _gates._quota_block_reason("acc-1", caps, {}, {}, {"acc-1": 4}) is None
    assert _gates._quota_block_reason("acc-1", caps, {}, {}) is None


@pytest.mark.asyncio
async def test_a_pool_without_soft_candidates_skips_the_day_totals() -> None:
    await create_account(AccountCreate(account_id="acc-1", label="a", session_name="acc-1"))
    campaign_id = await _campaign()
    fleet = await settings_store.load_settings()

    pool = await engine._load_selection_pool(
        campaign_id, "@chan", ["acc-1"], datetime.now(UTC), fleet
    )

    assert dict(pool.day_totals) == {}


def test_the_soft_day_cap_ranks_after_the_other_quotas() -> None:
    order = _gates._BLOCK_PRIORITY

    assert order.index("quota_day") < order.index("quota_soft_day") < order.index("cooldown")


@pytest.mark.asyncio
async def test_the_board_card_names_the_soft_day_and_counts_against_its_cap() -> None:
    await _soft_account(at=datetime.now(UTC) - timedelta(days=1, hours=1))
    await create_account(AccountCreate(account_id="acc-2", label="b", session_name="acc-2"))
    campaign_id = await _campaign()
    await assign_account_to_campaign(campaign_id, "acc-2")

    board = await load_neurocomment_board(campaign_id)

    assert board is not None
    cards = {card.account_id: card for card in board.accounts}
    assert (cards["acc-1"].soft_start_day, cards["acc-1"].soft_start_days) == (2, 5)
    assert cards["acc-1"].max_comments_per_hour == 2
    assert (cards["acc-2"].soft_start_day, cards["acc-2"].soft_start_days) == (None, 0)
