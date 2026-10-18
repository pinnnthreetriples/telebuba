"""Activity sessions inside a signed-in container: read channels, watch stories, react.

The same rule as ``telegram_app``: every tap goes to a node the current dump showed.
A target the screen does not show (no channel row in the chat list, no stories row,
no post to react to) is skipped and named in the result, never guessed at; the one
area tapped without a node of its own is the right third of the full-screen story
viewer, and only while the chat list is not showing. The labels
are Telegram's accessibility text in English and Russian; they have not yet been
checked against a live container's dump.

Gestures follow one rule: a slow read is ``human_swipe`` (the Bezier curve as
``input motionevent``), a quick flick is ``human_fling`` (a short ``input swipe``
Android turns into scroll inertia). Pauses between reads come from
``sample_reading_pause_sec``.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from core.config import settings
from core.emulator import adb
from core.emulator._errors import EmulatorError
from core.emulator._input import HumanInputEngine
from core.emulator.telegram_app import read_screen
from core.emulator.ui_dump import UiNode
from schemas.emulator import PlaybookResult

if TYPE_CHECKING:
    from core.emulator.ui_dump import Screen
    from schemas.emulator import EmulatorRefusalCode, PlaybookSkip

_EMULATOR_FIELD_MISSING: EmulatorRefusalCode = "emulator_field_missing"
_EMULATOR_NOT_SIGNED_IN: EmulatorRefusalCode = "emulator_not_signed_in"

_SIGN_IN_SCREENS: frozenset[Screen] = frozenset({"intro", "phone", "confirm", "code", "password"})
# BACK presses that bring any channel, story or menu back to the chat list.
_MAX_BACKS = 5

_CHANNEL_PREFIXES = ("channel", "канал")
_STORY_WORDS = ("story", "stories", "истори")
# The own-story button opens the camera: never tapped.
_OWN_STORY_WORDS = ("my story", "add story", "моя история", "добавить историю")

_STORIES_SHARE = 0.35
_FLING_CHANCE = 0.2
_REACTION_CHANCE = 0.08
_FEED_SCROLLS = (3, 8)
# Where a read drags from and how far (shares of the screen height); a scroll back
# to re-read starts lower and goes a shorter way up.
_READ_START, _READ_LENGTH = 0.3, (0.3, 0.5)
_BACK_START, _BACK_LENGTH = 0.6, (0.15, 0.25)
# A post is a text node at least this share of the screen tall, between the header
# and the bottom bar.
_POST_MIN_HEIGHT = 0.08
_CONTENT_TOP, _CONTENT_BOTTOM = 0.12, 0.9


def _now() -> float:
    return asyncio.get_running_loop().time()


def _label(node: UiNode) -> str:
    return f"{node.text} {node.description}".strip().casefold()


def _screen_size(nodes: list[UiNode]) -> tuple[int, int]:
    """The screen's width and height as the dump's outermost box gives them."""
    if not nodes:
        raise EmulatorError(_EMULATOR_FIELD_MISSING)
    return max(node.bounds[2] for node in nodes), max(node.bounds[3] for node in nodes)


def channel_rows(nodes: list[UiNode]) -> list[UiNode]:
    """Chat-list rows whose accessibility text says they are channels."""
    return [node for node in nodes if _label(node).startswith(_CHANNEL_PREFIXES)]


def story_avatars(nodes: list[UiNode]) -> list[UiNode]:
    """Other people's stories in the row above the chat list; never the own-story button."""
    return [
        node
        for node in nodes
        if any(word in _label(node) for word in _STORY_WORDS)
        and not any(word in _label(node) for word in _OWN_STORY_WORDS)
    ]


def posts(nodes: list[UiNode]) -> list[UiNode]:
    """Text nodes tall enough to be a post, inside the feed between header and bottom bar."""
    width, height = _screen_size(nodes)
    return [
        node
        for node in nodes
        if node.text.strip()
        and node.bounds[3] - node.bounds[1] >= _POST_MIN_HEIGHT * height
        and node.bounds[1] >= _CONTENT_TOP * height
        and node.bounds[3] <= _CONTENT_BOTTOM * height
        and node.bounds[2] <= width
    ]


class BaseMobilePlaybook(ABC):
    """One app's activity scenarios; ``run_session`` spends ``duration_sec`` in it."""

    def __init__(self, engine: HumanInputEngine | None = None) -> None:
        self.engine = engine or HumanInputEngine()
        self.result = PlaybookResult()

    @abstractmethod
    async def run_session(self, serial: str, duration_sec: int) -> PlaybookResult: ...

    def _skip(self, reason: PlaybookSkip) -> None:
        if reason not in self.result.skipped:
            self.result.skipped.append(reason)


class TelegramMobilePlaybook(BaseMobilePlaybook):
    """Telegram: channel feeds read with pauses, stories watched, an occasional reaction."""

    async def run_session(self, serial: str, duration_sec: int) -> PlaybookResult:
        """Alternate stories and channel reading until ``duration_sec`` runs out.

        Starts and ends on the chat list. Stops early when the chat list shows neither
        a channel nor a stories row: there is nothing left to do.
        """
        self.result = PlaybookResult()
        deadline = _now() + duration_sec
        await self.return_to_chats(serial)
        while _now() < deadline:
            first, second = self._open_stories, self._open_channel
            if self.engine.rng.random() >= _STORIES_SHARE:
                first, second = second, first
            if not await first(serial) and not await second(serial):
                break
            await self.return_to_chats(serial)
            pause, _scroll_back = self.engine.sample_reading_pause_sec()
            await asyncio.sleep(pause)
        return self.result

    async def _open_channel(self, serial: str) -> bool:
        _screen, nodes = await read_screen(serial)
        rows = channel_rows(nodes)
        if not rows:
            self._skip("no_channel")
            return False
        await adb.human_tap(serial, self.engine.rng.choice(rows), engine=self.engine)
        await self.read_channel_feed(serial, self.engine.rng.randint(*_FEED_SCROLLS))
        self.result.channels_read += 1
        return True

    async def _open_stories(self, serial: str) -> bool:
        before = self.result.stories_watched
        await self.watch_stories(serial)
        return self.result.stories_watched > before

    async def read_channel_feed(self, serial: str, scroll_count: int) -> None:
        """Read the open channel back through its history, one screen per pause.

        Mostly a slow Bezier drag, sometimes a flick; now and then a short scroll back
        to re-read, and rarely a reaction to a post on screen.
        """
        for _ in range(scroll_count):
            pause, scroll_back = self.engine.sample_reading_pause_sec()
            await asyncio.sleep(pause)
            screen, nodes = await read_screen(serial)
            if screen == "chats":  # the channel did not open, or was left
                return
            width, height = _screen_size(nodes)
            if self.engine.rng.random() < _REACTION_CHANCE:
                candidates = posts(nodes)
                if candidates:
                    await self.react_to_post(serial, self.engine.rng.choice(candidates))
                else:
                    self._skip("no_post")
            screen_size = (width, height)
            top, bottom = self.engine.sample_scroll(
                screen_size, _READ_START, _READ_LENGTH, downward=True
            )
            if self.engine.rng.random() < _FLING_CHANCE:
                await adb.human_fling(serial, top, bottom, engine=self.engine)
            else:
                await adb.human_swipe(serial, top, bottom, engine=self.engine)
            self.result.scrolls += 1
            if scroll_back:
                back_from, back_to = self.engine.sample_scroll(
                    screen_size, _BACK_START, _BACK_LENGTH, downward=False
                )
                await adb.human_swipe(serial, back_from, back_to, engine=self.engine)

    async def watch_stories(self, serial: str, max_stories: int = 5) -> None:
        """Open a story from the chat list and watch up to ``max_stories``, tapping on."""
        _screen, nodes = await read_screen(serial)
        avatars = story_avatars(nodes)
        if not avatars:
            self._skip("no_stories")
            return
        await adb.human_tap(serial, self.engine.rng.choice(avatars), engine=self.engine)
        for _ in range(self.engine.rng.randint(1, max(1, max_stories))):
            await asyncio.sleep(self.engine.sample_story_view_sec())
            screen, nodes = await read_screen(serial)
            if screen == "chats":  # the viewer never opened, or the stories ran out
                return
            width, height = _screen_size(nodes)
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
        """Press BACK until the chat list shows; never on a sign-in screen."""
        for _ in range(_MAX_BACKS + 1):
            screen, _nodes = await read_screen(serial)
            if screen == "chats":
                return
            if screen in _SIGN_IN_SCREENS:
                raise EmulatorError(_EMULATOR_NOT_SIGNED_IN)
            await adb.press(serial, adb.KEYCODE_BACK)
            await asyncio.sleep(settings.emulator.poll_seconds)
        raise EmulatorError(_EMULATOR_FIELD_MISSING)
