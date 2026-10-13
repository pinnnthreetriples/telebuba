"""``outreach`` repository: limits singleton and per-day usage counters."""

from __future__ import annotations

import pytest

from core.repositories.outreach import (
    add_outreach_usage,
    list_accounts_with_contact_matches,
    list_outreach_usage,
    load_outreach_limits,
    save_outreach_limits,
)
from schemas.outreach_limits import OutreachDailyUsage, OutreachLimits, OutreachLimitsUpdate


@pytest.mark.asyncio
async def test_unsaved_limits_are_the_defaults() -> None:
    limits = await load_outreach_limits()

    assert limits == OutreachLimits()
    assert limits.contact_imports_per_day == 15
    assert limits.lookup_batch_size == 3
    assert (limits.lookup_min_delay_seconds, limits.lookup_max_delay_seconds) == (180, 480)
    assert limits.silent_limit_streak == 8
    assert limits.first_messages_per_day == 25
    assert (limits.bulk_min_delay_seconds, limits.bulk_max_delay_seconds) == (60, 180)
    assert limits.updated_at is None


@pytest.mark.asyncio
async def test_a_save_replaces_every_field_and_a_second_save_updates_the_row() -> None:
    await save_outreach_limits(OutreachLimitsUpdate(contact_imports_per_day=10))
    saved = await save_outreach_limits(
        OutreachLimitsUpdate(lookup_batch_size=5, first_messages_per_day=None),
    )

    loaded = await load_outreach_limits()
    assert loaded == saved
    assert loaded.updated_at is not None
    # The second save sent the default back, so the first save's 10 is gone.
    assert loaded.contact_imports_per_day == 15
    assert loaded.lookup_batch_size == 5
    assert loaded.first_messages_per_day is None


def test_limits_reject_a_pause_range_upside_down() -> None:
    with pytest.raises(ValueError, match="max delay"):
        OutreachLimitsUpdate(lookup_min_delay_seconds=600, lookup_max_delay_seconds=60)
    with pytest.raises(ValueError, match="max delay"):
        OutreachLimitsUpdate(bulk_min_delay_seconds=600, bulk_max_delay_seconds=60)
    with pytest.raises(ValueError, match="less than or equal to 3600"):
        OutreachLimitsUpdate(lookup_max_delay_seconds=3601)


@pytest.mark.asyncio
async def test_usage_increments_accumulate_per_account_and_day() -> None:
    day = "2026-10-12"
    await add_outreach_usage(OutreachDailyUsage(account_id="a1", day=day, contact_imports=3))
    await add_outreach_usage(
        OutreachDailyUsage(account_id="a1", day=day, contact_imports=2, contact_matches=1),
    )
    await add_outreach_usage(OutreachDailyUsage(account_id="a1", day=day, first_messages=1))
    await add_outreach_usage(
        OutreachDailyUsage(account_id="a1", day="2026-10-11", contact_imports=9)
    )

    usage = await list_outreach_usage(["a1", "a2"], day)

    assert usage == {
        "a1": OutreachDailyUsage(
            account_id="a1", day=day, contact_imports=5, contact_matches=1, first_messages=1
        ),
        # No row yet: zeros, so the caller can subtract without a special case.
        "a2": OutreachDailyUsage(account_id="a2", day=day),
    }


@pytest.mark.asyncio
async def test_accounts_with_contact_matches_look_across_every_day() -> None:
    await add_outreach_usage(
        OutreachDailyUsage(account_id="a1", day="2026-01-01", contact_matches=1),
    )
    await add_outreach_usage(
        OutreachDailyUsage(account_id="a2", day="2026-10-12", contact_imports=15),
    )

    assert await list_accounts_with_contact_matches(["a1", "a2", "a3"]) == {"a1"}
