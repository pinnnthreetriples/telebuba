"""Activity sessions inside a signed-in container: read channels, watch stories, react.

The same rule as ``telegram_app``: every tap goes to a node the current dump showed.
A target the screen does not show (no channel row in the chat list, no stories row,
no post to react to) is skipped and named in the result, never guessed at; the one
area tapped without a node of its own is the right third of the full-screen story
viewer, and only while the chat list is not showing. The labels are Telegram's own
strings in every language a container may run (``ui_dump.labels``); they have not
yet been checked against a live container's dump.

A session follows its ``PlaybookPlan``: the warming visit's channels and action
budget, the persona's reaction chance (one reaction per channel at most: a second
quick reaction takes the first back), whether stories are watched, and in which
order. A channel is read once a session, the stories row is opened once. The plan's
other scenarios (a search, a photo or note, a folder, a Mini App: ``_scenarios``) are
mixed in at random, each within the session's time. An error dialog or an app that
stays offline ends the session early with what it did.

Gestures follow one rule: a slow read is ``human_swipe`` (the Bezier curve as
``input motionevent``), a quick flick is ``human_fling`` (a short ``input swipe``
Android turns into scroll inertia). Pauses between reads come from
``sample_reading_pause_sec``, stretched by how much text the screen shows.
"""

from __future__ import annotations

import asyncio
import math
from typing import TYPE_CHECKING

from core.config import settings
from core.emulator import adb
from core.emulator._errors import EmulatorError
from core.emulator._input import HumanInputEngine
from core.emulator._scenarios import ScenarioSteps
from core.emulator._selectors import channel_rows, node_label, offline, screen_size
from core.emulator.telegram_app import read_screen
from core.emulator.ui_dump import UiNode, labels
from schemas.emulator import PlaybookPlan, PlaybookResult

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from core.emulator.ui_dump import Screen
    from schemas.emulator import EmulatorRefusalCode

__all__ = ["TelegramMobilePlaybook", "channel_rows", "posts", "story_avatars"]

_EMULATOR_FIELD_MISSING: EmulatorRefusalCode = "emulator_field_missing"
_EMULATOR_NOT_SIGNED_IN: EmulatorRefusalCode = "emulator_not_signed_in"

_SIGN_IN_SCREENS: frozenset[Screen] = frozenset({"intro", "phone", "confirm", "code", "password"})
# BACK presses that bring any channel, story or menu back to the chat list.
_MAX_BACKS = 5

_STORIES_SHARE = 0.35
# The chance a turn starts with one of the plan's other scenarios, not stories or a
# channel; and that a channel read ends on a photo or note.
_SCENARIO_SHARE = 0.3
_MEDIA_SHARE = 0.4
_FLING_CHANCE = 0.2
_FEED_SCROLLS = (3, 8)
# Where a read drags from and how far (shares of the screen height); a scroll back
# to re-read starts lower and goes a shorter way up.
_READ_START, _READ_LENGTH = 0.3, (0.3, 0.5)
_BACK_START, _BACK_LENGTH = 0.6, (0.15, 0.25)
# A post is a text node at least this share of the screen tall, between the header
# and the bottom bar.
_POST_MIN_HEIGHT = 0.08
_CONTENT_TOP, _CONTENT_BOTTOM = 0.12, 0.9
# The stories row sits above the chat list, in this top share of the screen.
_STORIES_BAND = 0.3
# A reading pause times this factor: a picture is glanced at, a long text read;
# every this many characters on screen add one to the factor, up to the cap.
_TEXT_FACTOR_MIN, _TEXT_FACTOR_MAX = 0.6, 2.0
_CHARS_PER_FACTOR = 500
# A feed is dumped every this many screens (and always where it reacts): between two
# dumps the last one stands for the screen, as one swipe moves it less than a page.
_DUMP_EVERY = 2


def _now() -> float:
    return asyncio.get_running_loop().time()


def story_avatars(nodes: list[UiNode]) -> list[UiNode]:
    """Other people's stories in the row above the chat list; never the own-story button.

    Only inside the top band, and never a chat row: a channel called "History" is
    no story.
    """
    if not nodes:
        return []
    _width, height = screen_size(nodes)
    words = labels()
    rows = set(channel_rows(nodes))
    return [
        node
        for node in nodes
        if node not in rows
        and node.bounds[3] <= _STORIES_BAND * height
        and any(word in node_label(node) for word in words.story)
        and not any(word in node_label(node) for word in words.own_story)
    ]


def posts(nodes: list[UiNode]) -> list[UiNode]:
    """Text nodes tall enough to be a post, inside the feed between header and bottom bar."""
    width, height = screen_size(nodes)
    return [
        node
        for node in nodes
        if node.text.strip()
        and node.bounds[3] - node.bounds[1] >= _POST_MIN_HEIGHT * height
        and node.bounds[1] >= _CONTENT_TOP * height
        and node.bounds[3] <= _CONTENT_BOTTOM * height
        and node.bounds[2] <= width
    ]


def _channel_key(row: UiNode) -> str:
    """The channel's title in its row ("Channel. <title>. ..."): unread counts change."""
    parts = node_label(row).split(". ")
    return parts[1] if len(parts) > 1 else parts[0]


class TelegramMobilePlaybook(ScenarioSteps):
    """Telegram: channel feeds read with pauses, stories watched, an occasional reaction."""

    def __init__(self, engine: HumanInputEngine | None = None) -> None:
        self.engine = engine or HumanInputEngine()
        self.result = PlaybookResult()
        self.plan = PlaybookPlan()
        # No end until a session sets one: a feed read on its own runs its scrolls.
        self._deadline = math.inf
        self._read: set[str] = set()
        self._stories_left = True
        # The chat list the last dump showed, good until the next gesture.
        self._chats: list[UiNode] | None = None
        self._start_scenarios(self.plan)

    async def run_session(
        self, serial: str, duration_sec: int, plan: PlaybookPlan | None = None
    ) -> PlaybookResult:
        """Alternate stories and channel reading until ``duration_sec`` or ``plan`` runs out.

        Starts and ends on the chat list. A channel is read at most once and the
        stories are watched at most once a session; stops early when there is nothing
        left to open, or the plan's channels or actions are spent.
        """
        self.result = PlaybookResult()
        self.plan = plan or PlaybookPlan()
        self._deadline = _now() + duration_sec
        self._read = set()
        self._stories_left = self.plan.stories
        self._start_scenarios(self.plan)
        await self.return_to_chats(serial)
        if self.plan.stories_first and not self._halted and await self._open_stories(serial):
            await self.return_to_chats(serial)
        while self._time_left() and not self._actions_spent() and not self._halted:
            if not await self._take_turn(serial):
                break
            await self.return_to_chats(serial)
            if not self._time_left() or self._halted:
                break
            pause, _scroll_back = self.engine.sample_reading_pause_sec()
            await asyncio.sleep(pause)
        return self.result

    async def _take_turn(self, serial: str) -> bool:
        """Stories or a channel, the other one when the first finds nothing.

        The plan's other scenarios come first now and then, else as the last resort.
        """
        rng = self.engine.rng
        turn: list[Callable[[str], Awaitable[bool]]] = [self._open_stories, self._open_channel]
        if rng.random() >= _STORIES_SHARE:
            turn.reverse()
        scenarios = self._scenarios()
        if scenarios:
            rng.shuffle(scenarios)
            turn = [*scenarios, *turn] if rng.random() < _SCENARIO_SHARE else [*turn, *scenarios]
        for activity in turn:
            if await activity(serial):
                return True
            if self._halted:
                return False
        return False

    async def _screen(self, serial: str) -> tuple[Screen, list[UiNode]]:
        return await read_screen(serial)

    async def _chat_list(self, serial: str) -> list[UiNode]:
        """The chat list ``return_to_chats`` just dumped, or a fresh dump."""
        if self._chats is not None:
            return self._chats
        _screen, nodes = await read_screen(serial)
        return nodes

    def _seconds_left(self) -> float:
        return self._deadline - _now()

    def _time_left(self) -> bool:
        return _now() < self._deadline

    def _actions_spent(self) -> bool:
        cap = self.plan.max_actions
        return cap is not None and self.result.channels_read + self.result.reactions >= cap

    async def _open_channel(self, serial: str) -> bool:
        if self.plan.channels is not None and self.result.channels_read >= self.plan.channels:
            return False
        nodes = await self._chat_list(serial)
        rows = [row for row in channel_rows(nodes) if _channel_key(row) not in self._read]
        if not rows:
            self._skip("no_channel")
            return False
        row = self.engine.rng.choice(rows)
        self._chats = None
        self._read.add(_channel_key(row))
        react = self.engine.rng.random() < self.plan.reaction_probability
        await adb.human_tap(serial, row, engine=self.engine)
        self.result.channels_read += 1
        scrolls = self.engine.rng.randint(*_FEED_SCROLLS)
        await self.read_channel_feed(serial, scrolls, react=react)
        if self.plan.media and self._time_left() and self.engine.rng.random() < _MEDIA_SHARE:
            await self.consume_feed_media(serial)
        return True

    async def _open_stories(self, serial: str) -> bool:
        if not self._stories_left:
            return False
        self._stories_left = False
        before = self.result.stories_watched
        await self.watch_stories(serial)
        return self.result.stories_watched > before

    def _reading_pause(self, nodes: list[UiNode]) -> tuple[float, bool]:
        """The engine's pause, longer for a screen full of text, shorter for a picture."""
        pause, scroll_back = self.engine.sample_reading_pause_sec()
        chars = sum(len(node.text) for node in posts(nodes))
        factor = min(_TEXT_FACTOR_MAX, _TEXT_FACTOR_MIN + chars / _CHARS_PER_FACTOR)
        return pause * factor, scroll_back

    async def read_channel_feed(
        self, serial: str, scroll_count: int, *, react: bool = False
    ) -> None:
        """Read the open channel back through its history, one screen per pause.

        Each screen is read for as long as its text takes, then scrolled: mostly a
        slow Bezier drag, sometimes a flick, now and then a short scroll back to
        re-read. With ``react``, one post on one of the screens gets a reaction and
        never a second: Telegram takes a repeated quick reaction back.
        """
        react_at = self.engine.rng.randrange(scroll_count) if react and scroll_count else None
        nodes: list[UiNode] = []
        for scroll in range(scroll_count):
            if not self._time_left():
                return
            reacting = react_at is not None and scroll >= react_at
            if not nodes or reacting or scroll % _DUMP_EVERY == 0:
                screen, nodes = await read_screen(serial)
                if screen == "chats":  # the channel did not open, or was left
                    return
            pause, scroll_back = self._reading_pause(nodes)
            await asyncio.sleep(pause)
            if react_at is not None and scroll >= react_at and not self._actions_spent():
                candidates = posts(nodes)
                if candidates:
                    await self.react_to_post(serial, self.engine.rng.choice(candidates))
                    react_at = None
                else:
                    self._skip("no_post")
            size = screen_size(nodes)
            top, bottom = self.engine.sample_scroll(size, _READ_START, _READ_LENGTH, downward=True)
            if self.engine.rng.random() < _FLING_CHANCE:
                await adb.human_fling(serial, top, bottom, engine=self.engine)
            else:
                await adb.human_swipe(serial, top, bottom, engine=self.engine)
            self.result.scrolls += 1
            if scroll_back:
                back_from, back_to = self.engine.sample_scroll(
                    size, _BACK_START, _BACK_LENGTH, downward=False
                )
                await adb.human_swipe(serial, back_from, back_to, engine=self.engine)

    async def watch_stories(self, serial: str, max_stories: int = 5) -> None:
        """Open a story from the chat list and watch up to ``max_stories``, tapping on."""
        avatars = story_avatars(await self._chat_list(serial))
        if not avatars:
            self._skip("no_stories")
            return
        self._chats = None
        await adb.human_tap(serial, self.engine.rng.choice(avatars), engine=self.engine)
        for _ in range(self.engine.rng.randint(1, max(1, max_stories))):
            if not self._time_left():
                return
            await asyncio.sleep(self.engine.sample_story_view_sec())
            screen, nodes = await read_screen(serial)
            if screen == "chats":  # the viewer never opened, or the stories ran out
                return
            width, height = screen_size(nodes)
            # The viewer's right third moves to the next story.
            next_zone = (round(width * 0.7), round(height * 0.3), width, round(height * 0.7))
            await adb.human_tap(
                serial, UiNode("", "", "", password=False, bounds=next_zone), engine=self.engine
            )
            self.result.stories_watched += 1

    async def react_to_post(self, serial: str, node: UiNode) -> None:
        """Double-tap the post: Telegram's quick reaction."""
        await adb.double_tap(serial, node, engine=self.engine)
        self.result.reactions += 1

    async def return_to_chats(self, serial: str) -> None:
        """Press BACK until the chat list shows; never on a sign-in screen.

        An error dialog on the way is closed and ends the session (``app_error``); a
        chat list that keeps waiting for the network past ``ui_timeout_seconds`` ends
        it too (``app_offline``).
        """
        self._chats = None
        for _ in range(_MAX_BACKS + 1):
            screen, nodes = await read_screen(serial)
            if await self._dismiss_error(serial, nodes):
                continue
            if screen == "chats":
                self._chats = await self._wait_online(serial, nodes)
                return
            if screen in _SIGN_IN_SCREENS:
                raise EmulatorError(_EMULATOR_NOT_SIGNED_IN)
            await adb.press(serial, adb.KEYCODE_BACK)
            await asyncio.sleep(settings.emulator.poll_seconds)
        raise EmulatorError(_EMULATOR_FIELD_MISSING)

    async def _wait_online(self, serial: str, nodes: list[UiNode]) -> list[UiNode]:
        """The chat list once it is online; ends the session if it stays offline."""
        deadline = _now() + settings.emulator.ui_timeout_seconds
        while offline(nodes):
            if _now() >= deadline:
                self._halt("app_offline")
                break
            await asyncio.sleep(settings.emulator.poll_seconds)
            _screen, nodes = await read_screen(serial)
        return nodes
