"""Write extras runners — action shapes, budget booking, the two-dispatch draft."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from schemas.telegram_actions_warming import WarmForwardToSaved, WarmSaveDraft, WarmSelfNote
from services.warming import _extras, _extras_writes, _seams
from services.warming._extras import EXTRAS, run_extras_step
from services.warming._steps import _ChannelTally
from tests.services.warming._support import _Recorder
from tests.services.warming.test_extras_step import _RECENT_IDS, _ctx, _extras_range

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult, TelegramAction

_WRITE_SPECS = tuple(spec for spec in EXTRAS if spec.kind == "write")
# The id-bearing channel comes first: the seeded ``choice`` over two items picks index
# 1, so an unfiltered draw would land on the empty one and fail the schema.
_MIXED_IDS = {"full": list(range(1, 9)), "empty": []}


def _one(recorder: _Recorder) -> TelegramAction:
    ((_id, action),) = recorder.actions
    return action


@pytest.mark.asyncio
async def test_saved_writes_a_configured_note_and_books_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    tally = _ChannelTally()

    await _extras_writes.saved(_ctx(tally=tally))

    note = _one(recorder)
    assert isinstance(note, WarmSelfNote)
    assert note.text in settings.warming.extras_note_texts
    assert (note.schedule_in_hours, note.cancel_reminder) == (None, False)
    assert tally.attempts == 1


@pytest.mark.parametrize(
    ("draw", "probability", "expected"), [(0.0, 0.5, True), (0.7, 0.5, False), (0.0, 0.0, False)]
)
@pytest.mark.asyncio
async def test_scheduled_draws_its_delay_from_the_window_and_cancels_by_probability(
    monkeypatch: pytest.MonkeyPatch, draw: float, probability: float, *, expected: bool
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(_seams.rng, "random", lambda: draw)
    monkeypatch.setattr(settings.warming, "extras_scheduled_delay_hours", (2.0, 3.0))
    monkeypatch.setattr(settings.warming, "extras_reminder_cancel_probability", probability)
    tally = _ChannelTally()

    await _extras_writes.scheduled(_ctx(tally=tally))

    note = _one(recorder)
    assert isinstance(note, WarmSelfNote)
    assert note.text in settings.warming.extras_note_texts
    assert note.schedule_in_hours is not None
    assert 2.0 <= note.schedule_in_hours <= 3.0
    assert note.cancel_reminder is expected
    assert tally.attempts == 1


@pytest.mark.asyncio
async def test_drafts_types_then_clears_after_a_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    order: list[str] = []

    async def execute(account_id: str, action: TelegramAction) -> ActionResult:
        order.append(action.action_type)
        return await recorder.execute(account_id, action)

    async def pause(_lo: float, _hi: float) -> None:
        order.append("pause")

    monkeypatch.setattr(_seams, "execute", execute)
    monkeypatch.setattr(_extras_writes, "_human_pause", pause)
    tally = _ChannelTally()

    result = await _extras_writes.drafts(_ctx(tally=tally))  # rng.random pinned → 0.0

    assert order == ["warm_save_draft", "pause", "warm_save_draft"]
    typed, cleared = (a for _id, a in recorder.actions)
    assert isinstance(typed, WarmSaveDraft)
    assert isinstance(cleared, WarmSaveDraft)
    assert typed.text in settings.warming.extras_note_texts
    assert cleared.text == ""
    # Both dispatches booked, and the caller folds the clear's outcome.
    assert tally.attempts == 2
    assert result.action_type == "warm_save_draft"


@pytest.mark.asyncio
async def test_drafts_keeps_the_draft_when_the_draw_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(_seams.rng, "random", lambda: 0.9)
    tally = _ChannelTally()

    await _extras_writes.drafts(_ctx(tally=tally))

    draft = _one(recorder)
    assert isinstance(draft, WarmSaveDraft)
    assert draft.text != ""
    assert tally.attempts == 1


@pytest.mark.asyncio
async def test_drafts_stops_after_a_failed_first_write(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    recorder.flood_on = {"warm_save_draft"}
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    tally = _ChannelTally()

    result = await _extras_writes.drafts(_ctx(tally=tally))

    assert recorder.types() == ["warm_save_draft"]
    assert result.status == "flood_wait"
    assert tally.attempts == 1


@pytest.mark.asyncio
async def test_drafts_skips_the_clear_when_the_budget_is_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One action left: the draft books it; the clear would overspend, so it is skipped."""
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    tally = _ChannelTally()

    await _extras_writes.drafts(_ctx(tally=tally, remaining=1))

    assert recorder.types() == ["warm_save_draft"]
    assert tally.attempts == 1


@pytest.mark.asyncio
async def test_forward_targets_only_a_post_the_account_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    tally = _ChannelTally()

    await _extras_writes.forward(_ctx(tally=tally, recent_ids=_MIXED_IDS))

    action = _one(recorder)
    assert isinstance(action, WarmForwardToSaved)
    assert action.channel == "full"
    assert action.message_id in _MIXED_IDS["full"][:5]
    assert tally.attempts == 1


# --- the step with real write specs -------------------------------------------


@pytest.mark.asyncio
async def test_spent_budget_skips_every_write_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_extras, "EXTRAS", _WRITE_SPECS)
    _extras_range(monkeypatch, 20, 20)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    tally = _ChannelTally()

    landed = await run_extras_step(_ctx(tally=tally, remaining=0, recent_ids=_RECENT_IDS))

    assert landed is False
    assert recorder.actions == []
    assert tally.attempts == 0


@pytest.mark.asyncio
async def test_every_write_spec_books_before_dispatch_and_counts_when_it_lands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_extras, "EXTRAS", _WRITE_SPECS)
    _extras_range(monkeypatch, 20, 20)
    tally = _ChannelTally()
    seen: list[int] = []

    async def _execute(account_id: str, action: TelegramAction) -> ActionResult:
        seen.append(tally.attempts)  # already booked when the dispatcher sees it
        return await _Recorder().execute(account_id, action)

    monkeypatch.setattr(_seams, "execute", _execute)

    landed = await run_extras_step(_ctx(tally=tally, recent_ids=_RECENT_IDS))

    # Four specs, five RPCs (the pinned ``rng.random → 0.0`` fires the draft clear).
    assert landed is True
    assert seen == [1, 2, 3, 4, 5]
    assert tally.attempts == len(_WRITE_SPECS) + 1
    assert tally.extras == len(_WRITE_SPECS)


@pytest.mark.asyncio
async def test_a_flooded_write_halts_the_rest_of_the_step(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_extras, "EXTRAS", _WRITE_SPECS)
    _extras_range(monkeypatch, 20, 20)
    recorder = _Recorder()
    recorder.flood_on = {"warm_self_note", "warm_save_draft", "warm_forward_to_saved"}
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    tally = _ChannelTally()

    landed = await run_extras_step(_ctx(tally=tally, recent_ids=_RECENT_IDS))

    assert landed is False
    assert len(recorder.actions) == 1
    assert tally.attempts == 1
    assert tally.flooded is True
