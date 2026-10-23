"""What a phone visit's own outcome does to the account: limits, spend, card notes.

Reuses the fake pod of ``_mobile_support``; only what the session reports changes.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import upsert_warming_state
from schemas.emulator import PlaybookPlan, PlaybookResult
from schemas.warming import PhonePrep, WarmingStateWrite
from services.warming import _mobile_cycle, _mobile_prep_state, _transitions
from services.warming.board import load_board
from tests.services.warming._mobile_support import _iterate_on, _Pod, _record, _stops_done


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_a_limit_dialog_in_the_app_is_a_flood_wait(pod: _Pod) -> None:
    """Telegram's "too many attempts" on the phone cools the account down like on desktop."""
    pod.activity = PlaybookResult(channels_read=1, skipped=["app_error"])

    before = datetime.now(UTC)
    await _iterate_on("mobile")
    await _stops_done()

    record = await _record()
    assert (record.state, record.last_event) == ("flood_wait", "cycle:flood_wait")
    assert record.next_run_at is not None
    fallback = timedelta(hours=settings.warming.flood_wait_fallback_hours)
    assert datetime.fromisoformat(record.next_run_at) >= before + fallback
    assert record.daily_actions == 1, "what was done before the dialog is spent"


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_an_app_that_stays_offline_is_a_passing_failure_that_builds_the_streak(
    pod: _Pod,
) -> None:
    pod.activity = PlaybookResult(skipped=["app_offline"])

    for _ in range(_mobile_cycle._FAILURE_LIMIT - 1):
        await _iterate_on("mobile")
        await _stops_done()
        record = await _record()
        assert (record.state, record.last_error) == ("sleeping", "emulator_app_offline")
    await _iterate_on("mobile")
    await _stops_done()

    assert (await _record()).state == "error", "a proxy that stays dead needs the operator"


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon", "pod")
async def test_a_stop_mid_session_keeps_what_the_phone_already_did_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def cut_off(
        account_id: str,
        duration_sec: int,
        plan: PlaybookPlan | None = None,
        *,
        opened: str | None = None,
        progress: PlaybookResult | None = None,
    ) -> PlaybookResult:
        del account_id, duration_sec, plan, opened
        assert progress is not None
        progress.channels_read, progress.reactions = 2, 1
        started.set()
        await asyncio.Event().wait()
        raise AssertionError

    monkeypatch.setattr(_mobile_cycle, "run_mobile_activity", cut_off)
    task = asyncio.create_task(_iterate_on("mobile"))
    await started.wait()
    task.cancel()
    await asyncio.wait({task}, timeout=1)
    await _stops_done()

    assert (await _record()).daily_actions == 3, "a dispatched tap is never handed back"


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_stories_and_notes_spend_the_budget_like_on_desktop(pod: _Pod) -> None:
    """One story glance is one action however many stories it shows; a played note is one."""
    pod.activity = PlaybookResult(
        channels_read=1, stories_watched=4, notes_played=1, media_consumed=3, searches_done=1
    )

    await _iterate_on("mobile")
    await _stops_done()

    assert (await _record()).daily_actions == 3, "a read, the story glance and the note"


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_a_phone_without_channels_says_so_on_its_card(pod: _Pod) -> None:
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="active", device_mode="mobile")
    )
    pod.activity = PlaybookResult(stories_watched=2, skipped=["no_channel"])

    await _iterate_on("mobile")
    await _stops_done()
    card = next(a for a in (await load_board()).warming if a.account_id == "acc-1")
    assert card.phone_no_channels

    pod.activity = PlaybookResult(channels_read=1)
    await _iterate_on("mobile")
    await _stops_done()
    card = next(a for a in (await load_board()).warming if a.account_id == "acc-1")
    assert not card.phone_no_channels, "a channel read lifts the note"


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_a_full_host_retries_soon_not_a_whole_visit_gap_later(
    pod: _Pod, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "max_running_pods", 1)
    monkeypatch.setattr(settings.warming, "active_hours_enabled", False)
    _mobile_cycle._RUNNING.add("acc-other")

    before = datetime.now(UTC)
    await _iterate_on("mobile")

    record = await _record()
    assert record.last_error == "emulator_capacity"
    assert record.next_run_at is not None
    wait = datetime.fromisoformat(record.next_run_at) - before
    assert wait <= timedelta(seconds=_transitions.CAPACITY_RETRY_SECONDS[1] + 1)
    assert pod.calls == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("telethon")
async def test_a_visit_that_found_nothing_to_do_clears_a_stale_prep_failure(pod: _Pod) -> None:
    _mobile_prep_state.STATE["acc-1"] = PhonePrep(status="failed", code="emulator_boot_timeout")
    pod.activity = PlaybookResult(skipped=["no_channel", "no_stories"])

    await _iterate_on("mobile")
    await _stops_done()

    assert _mobile_prep_state.phone_prep("acc-1") is None, "the phone opened and ran"
