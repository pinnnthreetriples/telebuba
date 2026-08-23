"""Per-account cap overrides — resolution, both join gates, and the operator view.

The fleet caps already have their own suites (``test_onboarding_join_cap`` for joins,
``test_claim_integrity`` for the comment quota). These tests only cover what the override
layer adds: that an account's own number wins over the fleet's, that absence still means
"follow the fleet", and that the modal's view reports the spend and the reset moment
against the number the gates are actually enforcing.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    _get_engine,
    account_join_window,
    assign_account_to_campaign,
    claim_comment,
    create_account,
    create_campaign,
    link_channel_to_campaign,
    load_account_limit_override,
    load_account_limit_overrides,
    record_join,
)
from core.repositories.neurocomment._tables import _neurocomment_account_limits
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.neurocomment_limits import (
    AccountLimitOverride,
    AccountLimitsUpdate,
    AccountLimitsView,
)
from services._account_limits import account_join_cap, resolve_limits
from services.neurocomment import _gates, _seams, engine, limits, onboarding, settings_store
from services.neurocomment.board import load_neurocomment_board
from services.neuroshilling._join_cap import at_join_cap
from tests.services.neurocomment.onboarding_support import _JoinStub, _ReadStub

if TYPE_CHECKING:
    from collections.abc import Coroutine

pytestmark = pytest.mark.usefixtures("isolate_onboarding")


async def _account(account_id: str) -> None:
    await create_account(
        AccountCreate(account_id=account_id, label=account_id, session_name=account_id),
    )


async def _override(account_id: str, **caps: int | None) -> None:
    assert await limits.save_account_limits(account_id, AccountLimitsUpdate(**caps)) is not None


async def _view(account_id: str) -> AccountLimitsView:
    view = await limits.load_account_limits(account_id)
    assert view is not None
    return view


def _write_raw_override(account_id: str, **caps: int) -> Coroutine[None, None, None]:
    """Store an override the API would refuse — the legacy row a read must survive."""

    async def _write() -> None:
        def _insert() -> None:
            with _get_engine().begin() as connection:
                connection.execute(
                    _neurocomment_account_limits.insert().values(
                        account_id=account_id, updated_at="2026-01-01T00:00:00+00:00", **caps
                    ),
                )

        await asyncio.to_thread(_insert)

    return _write()


@pytest.mark.asyncio
async def test_resolve_falls_back_to_the_fleet_for_every_unset_cap() -> None:
    """No row, and a row of ``None``s, both mean "this account follows the fleet"."""
    fleet = await settings_store.load_settings()
    from_nothing = resolve_limits(None, fleet)
    from_empty = resolve_limits(AccountLimitOverride(account_id="acc-1"), fleet)

    assert from_nothing == from_empty
    assert from_nothing.max_comments_per_hour == fleet.max_comments_per_hour
    assert from_nothing.max_joins_per_day == settings.neurocomment.max_joins_per_account_per_day


@pytest.mark.asyncio
async def test_zero_is_a_value_and_not_an_absence() -> None:
    """``0`` turns a cap OFF; only ``None`` hands the decision back to the fleet.

    The two are one keystroke apart in the modal and opposite in effect, which is why
    the column is nullable rather than defaulted to zero.
    """
    fleet = await settings_store.load_settings()
    override = AccountLimitOverride(account_id="acc-1", max_joins_per_day=0)

    assert resolve_limits(override, fleet).max_joins_per_day == 0


@pytest.mark.asyncio
async def test_account_override_beats_the_fleet_join_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """One account may join past the fleet number, and another may be held below it."""
    monkeypatch.setattr(settings.neurocomment, "max_joins_per_account_per_day", 2)
    for account_id in ("acc-generous", "acc-tight"):
        await _account(account_id)
        await record_join(account_id)
        await record_join(account_id)  # both sit exactly on the fleet cap
    await _override("acc-generous", max_joins_per_day=5)
    await _override("acc-tight", max_joins_per_day=1)

    assert await account_join_cap("acc-generous", 2) == 5
    assert await account_join_cap("acc-tight", 2) == 1
    # The untouched account keeps the caller's own fleet number.
    assert await account_join_cap("acc-untouched", 2) == 2


@pytest.mark.asyncio
async def test_onboarding_joins_past_the_fleet_cap_on_an_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The override is enforced where it matters: the join RPC actually fires."""
    monkeypatch.setattr(settings.neurocomment, "max_joins_per_account_per_day", 1)
    await _account("acc-1")
    await record_join("acc-1")  # at the fleet cap, under its own
    await _override("acc-1", max_joins_per_day=3)

    read = _ReadStub(linked_chat_id=500, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)

    result = await onboarding.onboard_account_channel("acc-1", "@chan")

    assert [acc for acc, _ in join.calls] == ["acc-1"]
    assert result.reason != "daily_join_cap"


@pytest.mark.asyncio
async def test_neuroshilling_honours_the_same_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """One shared join log, one override — the other feature cannot read a different budget."""
    monkeypatch.setattr(settings.neuroshilling, "max_joins_per_account_per_day", 10)
    await _account("acc-1")
    for _ in range(3):
        await record_join("acc-1")

    assert await at_join_cap("acc-1") is False
    await _override("acc-1", max_joins_per_day=3)
    assert await at_join_cap("acc-1") is True


@pytest.mark.asyncio
async def test_clearing_every_cap_removes_the_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """«Вернуть общие» leaves the account indistinguishable from one never tuned."""
    monkeypatch.setattr(settings.neurocomment, "max_joins_per_account_per_day", 20)
    await _account("acc-1")
    await _override("acc-1", max_joins_per_day=30)
    assert (await _view("acc-1")).joins.overridden is True

    view = await limits.save_account_limits("acc-1", AccountLimitsUpdate())

    assert view is not None
    assert view.joins.overridden is False
    assert view.joins.limit == 20
    assert await account_join_cap("acc-1", 20) == 20


@pytest.mark.asyncio
async def test_view_reports_spend_and_the_moment_a_slot_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rolling window has no midnight: the reset is the oldest row's stamp plus 24h."""
    monkeypatch.setattr(settings.neurocomment, "max_joins_per_account_per_day", 20)
    await _account("acc-1")
    await record_join("acc-1")
    await record_join("acc-1")

    view = await _view("acc-1")

    assert (view.joins.used, view.joins.limit) == (2, 20)
    assert view.joins.resets_at is not None
    resets = datetime.fromisoformat(view.joins.resets_at)
    # The first slot comes back a day after the FIRST join, not a day from now.
    assert timedelta(hours=23) < resets - datetime.now(UTC) <= timedelta(hours=24)


@pytest.mark.asyncio
async def test_view_names_the_channel_its_per_pair_gauge_measured() -> None:
    """The day cap is per (account, channel), so the number is meaningless unnamed."""
    await _account("acc-1")
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@busy")
    await claim_comment("@busy", 1, campaign.campaign_id, "acc-1")
    await claim_comment("@busy", 2, campaign.campaign_id, "acc-1")
    await claim_comment("@quiet", 3, campaign.campaign_id, "acc-1")

    view = await _view("acc-1")

    assert view.busiest_channel == "@busy"
    assert view.comments_per_channel_per_day.used == 2


@pytest.mark.asyncio
async def test_empty_windows_have_nothing_to_wait_for() -> None:
    """An account that has spent nothing reports no reset moment rather than a fake one."""
    await _account("acc-1")

    view = await _view("acc-1")

    assert view.joins.used == 0
    assert view.joins.resets_at is None
    assert view.busiest_channel is None


@pytest.mark.asyncio
async def test_a_lowered_cap_reports_the_slot_that_actually_frees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Over the cap, the oldest row is not the one to wait for — every excess row is.

    Three joins and a cap of two means two of them have to age out before the account may
    join again, so the moment worth naming belongs to the SECOND oldest — neither the
    oldest (hours early, which is what this used to report) nor the newest.
    """
    monkeypatch.setattr(settings.neurocomment, "max_joins_per_account_per_day", 20)
    await _account("acc-1")
    for _ in range(3):
        await record_join("acc-1")
    day_ago = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    # The cap goes INTO the read, so the same window names a different row per cap.
    under = await account_join_window("acc-1", day_ago, 3)
    over = await account_join_window("acc-1", day_ago, 2)
    assert under.slot_at is not None
    assert over.slot_at is not None
    assert under.slot_at < over.slot_at
    await _override("acc-1", max_joins_per_day=2)

    view = await _view("acc-1")

    assert (view.joins.used, view.joins.limit) == (3, 2)
    assert (
        view.joins.resets_at
        == (datetime.fromisoformat(over.slot_at) + timedelta(days=1)).isoformat()
    )


@pytest.mark.asyncio
async def test_a_cap_above_the_stored_ceiling_still_reads_and_still_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stored row is data, not a request: reading it must never raise.

    The write model refuses an absurd cap, but a row that predates that bound is already
    in the table, and the bulk override load builds one model per candidate — so refusing
    to PARSE it would abort a whole selection pass, both join gates and the GET behind
    them, rather than affecting only its own account.
    """
    monkeypatch.setattr(settings.neurocomment, "max_joins_per_account_per_day", 20)
    await _account("acc-1")
    await _write_raw_override("acc-1", max_joins_per_day=50_000)

    assert await account_join_cap("acc-1", 20) == 50_000
    assert await load_account_limit_overrides(["acc-1"]) == {
        "acc-1": AccountLimitOverride(account_id="acc-1", max_joins_per_day=50_000),
    }
    assert (await _view("acc-1")).joins.limit == 50_000


@pytest.mark.asyncio
async def test_an_unknown_account_is_refused_rather_than_given_a_row() -> None:
    """The table has no foreign key, so a row written under a typo would never be collected.

    Refusing the write is the only thing that can keep it out; the route turns this into a
    404, the way every sibling per-account route does.
    """
    assert await limits.load_account_limits("ghost") is None
    assert await limits.save_account_limits("ghost", AccountLimitsUpdate(max_joins_per_day=5)) is (
        None
    )
    assert await load_account_limit_override("ghost") == AccountLimitOverride(account_id="ghost")


@pytest.mark.asyncio
async def test_the_pool_loads_day_counts_an_override_alone_asks_for() -> None:
    """Fleet cap off does not mean nobody enforces one — an override can switch it on.

    A pass that decided on the fleet number alone would score that account against
    per-channel counts it never read, which is a cap silently not applied.
    """
    await _account("acc-1")
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await claim_comment("@chan", 1, campaign.campaign_id, "acc-1")
    await _override("acc-1", max_comments_per_channel_per_day=1)
    fleet = (await settings_store.load_settings()).model_copy(
        update={"max_comments_per_channel_per_day": 0},
    )

    pool = await engine._load_selection_pool(
        campaign.campaign_id, "@chan", ["acc-1"], datetime.now(UTC), fleet
    )

    assert pool.daily_counts == {"acc-1": 1}
    assert (
        _gates._quota_block_reason(
            "acc-1", resolve_limits(pool.overrides["acc-1"], fleet), {}, pool.daily_counts
        )
        == "quota_day"
    )


@pytest.mark.asyncio
async def test_the_board_card_counts_against_the_account_own_cap() -> None:
    """The denominator on the card has to be the cap the engine applies to THAT account."""
    await _account("acc-1")
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="p"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await _override("acc-1", max_comments_per_hour=42)

    board = await load_neurocomment_board(campaign.campaign_id)

    assert board is not None
    assert [card.max_comments_per_hour for card in board.accounts] == [42]
