"""Per-account cap overrides (#58) — resolution, both join gates, and the operator view.

The fleet caps already have their own suites (``test_onboarding_join_cap`` for joins,
``test_claim_integrity`` for the comment quota). These tests only cover what the override
layer adds: that an account's own number wins over the fleet's, that absence still means
"follow the fleet", and that the modal's view reports the spend and the reset moment
against the number the gates are actually enforcing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import (
    claim_comment,
    create_account,
    create_campaign,
    link_channel_to_campaign,
    record_join,
)
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.neurocomment_limits import AccountLimitOverride, AccountLimitsUpdate
from services._account_limits import account_join_cap, resolve_limits
from services.neurocomment import _seams, limits, onboarding, settings_store
from services.neuroshilling._join_cap import at_join_cap
from tests.services.neurocomment.onboarding_support import _JoinStub, _ReadStub

pytestmark = pytest.mark.usefixtures("isolate_onboarding")


async def _account(account_id: str) -> None:
    await create_account(
        AccountCreate(account_id=account_id, label=account_id, session_name=account_id),
    )


async def _override(account_id: str, **caps: int | None) -> None:
    await limits.save_account_limits(account_id, AccountLimitsUpdate(**caps))


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
    assert (await limits.load_account_limits("acc-1")).joins.overridden is True

    view = await limits.save_account_limits("acc-1", AccountLimitsUpdate())

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

    view = await limits.load_account_limits("acc-1")

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

    view = await limits.load_account_limits("acc-1")

    assert view.busiest_channel == "@busy"
    assert view.comments_per_channel_per_day.used == 2


@pytest.mark.asyncio
async def test_empty_windows_have_nothing_to_wait_for() -> None:
    """An account that has spent nothing reports no reset moment rather than a fake one."""
    await _account("acc-1")

    view = await limits.load_account_limits("acc-1")

    assert view.joins.used == 0
    assert view.joins.resets_at is None
    assert view.busiest_channel is None
