"""``outreach`` repository: limits singleton and per-day usage counters."""

from __future__ import annotations

import pytest

from core.repositories.outreach import (
    PresetNameTakenError,
    add_outreach_usage,
    create_outreach_preset,
    delete_outreach_preset,
    list_accounts_with_contact_matches,
    list_outreach_presets,
    list_outreach_usage,
    load_outreach_limits,
    save_outreach_limits,
    update_outreach_preset,
)
from schemas.outreach_limits import OutreachDailyUsage, OutreachLimits, OutreachLimitsUpdate
from schemas.outreach_presets import OutreachPresetWrite
from schemas.run_schedule import RunSchedule


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


def _preset(name: str = "Branch", **values: int) -> OutreachPresetWrite:
    fields = {
        "pause_seconds": 150,
        "first_messages_per_day": 22,
        "contact_imports_per_day": 14,
        "lookup_batch_size": 3,
        **values,
    }
    return OutreachPresetWrite(name=name, **fields)


@pytest.mark.asyncio
async def test_presets_are_created_listed_by_name_updated_and_deleted() -> None:
    zeta = await create_outreach_preset(_preset("zeta"))
    alpha = await create_outreach_preset(_preset("  Alpha ", pause_seconds=60))

    assert [p.name for p in await list_outreach_presets()] == ["Alpha", "zeta"]
    assert alpha.pause_seconds == 60

    renamed = await update_outreach_preset(zeta.id, _preset("Центр", lookup_batch_size=5))
    assert renamed is not None
    assert (renamed.id, renamed.name, renamed.lookup_batch_size) == (zeta.id, "Центр", 5)
    assert await update_outreach_preset("missing", _preset("x")) is None

    assert await delete_outreach_preset(alpha.id) is True
    assert await delete_outreach_preset(alpha.id) is False
    assert [p.name for p in await list_outreach_presets()] == ["Центр"]


@pytest.mark.asyncio
async def test_preset_names_are_unique_regardless_of_case() -> None:
    first = await create_outreach_preset(_preset("Branch"))
    other = await create_outreach_preset(_preset("Other"))

    with pytest.raises(PresetNameTakenError):
        await create_outreach_preset(_preset("branch"))
    with pytest.raises(PresetNameTakenError):
        await update_outreach_preset(other.id, _preset("BRANCH"))
    # Keeping its own name is not a clash.
    assert await update_outreach_preset(first.id, _preset("Branch", pause_seconds=90))


def test_preset_values_stay_inside_the_slider_ranges() -> None:
    with pytest.raises(ValueError, match="greater than or equal to 10"):
        _preset(pause_seconds=9)
    with pytest.raises(ValueError, match="less than or equal to 20"):
        _preset(lookup_batch_size=21)
    with pytest.raises(ValueError, match="empty"):
        _preset("   ")


@pytest.mark.asyncio
async def test_the_default_run_schedule_is_saved_with_the_limits() -> None:
    assert (await load_outreach_limits()).schedule == RunSchedule()

    await save_outreach_limits(
        OutreachLimitsUpdate(
            schedule=RunSchedule(work_start="10:00", work_days=[0, 1, 2], concurrency=3)
        )
    )

    schedule = (await load_outreach_limits()).schedule
    assert (schedule.work_start, schedule.work_days, schedule.concurrency) == (
        "10:00",
        [0, 1, 2],
        3,
    )
    assert schedule.operator_timezone == "Europe/Moscow"
