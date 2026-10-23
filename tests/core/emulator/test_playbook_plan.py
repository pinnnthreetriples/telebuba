"""A session's plan and its cost: what a visit reads and reacts to, and how few dumps it takes.

Same scripted phone and fake clock as ``test_playbook``: the plan's channels, budget,
reactions and stories; a channel read once, one reaction per channel at most; reading
pauses by text; and a feed dumped every other screen, the chat list dump reused.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

import pytest

from core.emulator import playbook
from core.emulator._input import HumanInputEngine
from core.emulator.playbook import (
    TelegramMobilePlaybook,
    channel_rows,
    spent_actions,
    story_avatars,
)
from schemas.emulator import PlaybookPlan, PlaybookResult
from tests.core.emulator.test_playbook import (  # noqa: F401 - the clock fixture
    CHANNEL_ROW,
    MENU,
    NO_REACTIONS,
    POST,
    POST_PAUSE,
    ROOT,
    SERIAL,
    STORY,
    WIDTH,
    _Clock,
    _node,
    _opened_in_channel,
    _Phone,
    _phone,
    _playbook,
    clock,
)

if TYPE_CHECKING:
    from core.emulator.ui_dump import Screen, UiNode
    from tests.core.emulator.conftest import Console


OTHER_CHANNEL = _node(text="Channel. Daily Deals", bounds=(0, 720, WIDTH, 880))
HISTORY_CHANNEL = _node(text="Канал. История России", bounds=(0, 880, WIDTH, 1040))


def test_a_channel_named_history_is_no_story_and_stories_sit_on_top() -> None:
    low_story = _node(description="Stories by Bob", bounds=(180, 1500, 320, 1640))
    chats = [ROOT, MENU, STORY, HISTORY_CHANNEL, low_story]

    assert channel_rows(chats) == [HISTORY_CHANNEL], "the Russian row is a channel too"
    assert story_avatars(chats) == [STORY]


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_one_reaction_per_channel_however_long_it_is_read(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(playbook, "_FLING_CHANCE", 0.0)
    _opened_in_channel(_phone(monkeypatch, console, [CHANNEL_ROW]))
    session = _playbook()

    await session.read_channel_feed(SERIAL, 6, react=True)

    taps = [call for call in console.shell_calls() if call[:2] == ["input", "tap"]]
    assert len(taps) == 1, "a second quick reaction would take the first back"
    assert session.result.reactions == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_reactions_switched_off_are_never_sent(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(playbook, "_STORIES_SHARE", 0.0)
    _phone(monkeypatch, console, [CHANNEL_ROW, OTHER_CHANNEL])

    result = await _playbook().run_session(SERIAL, 600, NO_REACTIONS)

    assert result.reactions == 0
    assert not any(call[:2] == ["input", "tap"] for call in console.shell_calls())


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_the_visits_channels_and_action_budget_end_the_session(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(playbook, "_STORIES_SHARE", 0.0)
    _phone(monkeypatch, console, [CHANNEL_ROW, OTHER_CHANNEL])

    by_channels = await _playbook().run_session(
        SERIAL, 600, PlaybookPlan(channels=1, reaction_probability=0.0)
    )
    by_budget = await _playbook().run_session(
        SERIAL, 600, PlaybookPlan(max_actions=1, reaction_probability=0.0)
    )

    assert by_channels.channels_read == 1
    assert by_budget.channels_read == 1


@pytest.mark.asyncio
async def test_a_feed_stops_reading_when_the_session_time_is_up(
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
    clock: _Clock,  # noqa: F811
) -> None:
    monkeypatch.setattr(playbook, "_STORIES_SHARE", 0.0)
    monkeypatch.setattr(playbook, "_FEED_SCROLLS", (8, 8))
    _phone(monkeypatch, console, [CHANNEL_ROW])

    result = await _playbook().run_session(SERIAL, 3, NO_REACTIONS)

    assert 1 <= result.scrolls < 8, "the channel is left when the visit's time is up"
    assert clock.now < 3 + POST_PAUSE + 1, "no pause after the time is up"


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_stories_switched_off_are_never_opened(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(playbook, "_STORIES_SHARE", 1.0)
    _phone(monkeypatch, console, [STORY, CHANNEL_ROW])

    result = await _playbook().run_session(
        SERIAL, 600, PlaybookPlan(stories=False, reaction_probability=0.0)
    )

    assert result.stories_watched == 0
    assert result.channels_read == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_stories_first_are_watched_before_any_channel(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(playbook, "_STORIES_SHARE", 0.0)
    _phone(monkeypatch, console, [STORY, CHANNEL_ROW])

    result = await _playbook().run_session(
        SERIAL, 600, PlaybookPlan(stories_first=True, reaction_probability=0.0)
    )

    first_tap = console.shell_calls()[0]
    left, top, right, bottom = STORY.bounds
    assert left <= int(first_tap[2]) < right
    assert top <= int(first_tap[3]) < bottom
    assert result.stories_watched >= 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_the_story_glance_spends_one_action_of_the_budget(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As on desktop: one story view however many stories it shows, then the budget is gone."""
    _phone(monkeypatch, console, [STORY, CHANNEL_ROW])

    result = await _playbook().run_session(
        SERIAL, 600, PlaybookPlan(stories_first=True, max_actions=1, reaction_probability=0.0)
    )

    assert result.stories_watched >= 1
    assert result.channels_read == 0
    assert spent_actions(result) == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_a_spent_budget_opens_no_stories_first(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    _phone(monkeypatch, console, [STORY, CHANNEL_ROW])

    result = await _playbook().run_session(
        SERIAL, 600, PlaybookPlan(stories_first=True, max_actions=0, reaction_probability=0.0)
    )

    assert spent_actions(result) == 0


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_a_session_counts_into_the_progress_it_is_given(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller reads what was done so far even if the session never returns."""
    monkeypatch.setattr(playbook, "_STORIES_SHARE", 0.0)
    _phone(monkeypatch, console, [CHANNEL_ROW])
    progress = PlaybookResult()
    session = TelegramMobilePlaybook(_playbook().engine, progress=progress)

    result = await session.run_session(SERIAL, 600, NO_REACTIONS)

    assert result is progress
    assert progress.channels_read == 1


def test_a_long_text_is_read_longer_than_a_picture() -> None:
    session = _playbook()
    long_post = _node(text="x" * 1000, bounds=POST.bounds)
    picture = _node(bounds=POST.bounds)

    text_pause, _back = session._reading_pause([ROOT, long_post])
    glance, _back = session._reading_pause([ROOT, picture])

    assert text_pause == 2.0 * playbook._TEXT_FACTOR_MAX
    assert glance == 2.0 * playbook._TEXT_FACTOR_MIN


def _count_reads(monkeypatch: pytest.MonkeyPatch, phone: _Phone) -> list[int]:
    reads = [0]

    async def counted(serial: str) -> tuple[Screen, list[UiNode]]:
        reads[0] += 1
        return await phone.read_screen(serial)

    monkeypatch.setattr(playbook, "read_screen", counted)
    return reads


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_a_feed_is_dumped_every_other_screen(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    phone = _phone(monkeypatch, console, [CHANNEL_ROW])
    _opened_in_channel(phone)
    reads = _count_reads(monkeypatch, phone)

    await _playbook().read_channel_feed(SERIAL, 6)

    assert reads == [3], "one dump stands for two screens"


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_a_reaction_is_always_aimed_at_a_fresh_dump(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    phone = _phone(monkeypatch, console, [CHANNEL_ROW])
    _opened_in_channel(phone)
    reads = _count_reads(monkeypatch, phone)
    monkeypatch.setattr(playbook, "_DUMP_EVERY", 100)

    class _LateReaction(random.Random):
        """Puts the reaction on the second screen, the one no regular dump covers."""

        def randrange(self, start: int, stop: int | None = None, step: int = 1) -> int:
            return 1 if stop is None else super().randrange(start, stop, step)

    session = TelegramMobilePlaybook(HumanInputEngine(_LateReaction(7)))
    await session.read_channel_feed(SERIAL, 2, react=True)

    assert reads == [2], "the screen it reacts on is dumped again"
    assert session.result.reactions == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_the_chat_list_back_out_dump_is_reused_to_pick_a_channel(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(playbook, "_FEED_SCROLLS", (1, 1))
    phone = _phone(monkeypatch, console, [CHANNEL_ROW])
    reads = _count_reads(monkeypatch, phone)
    session = _playbook()

    await session.return_to_chats(SERIAL)
    assert reads == [1]
    await session._open_channel(SERIAL)

    assert reads == [2], "the chat list was not dumped again; only the channel was"
