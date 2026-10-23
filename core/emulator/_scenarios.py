"""The scenarios a session mixes in besides reading and stories (``TelegramMobilePlaybook``).

A global search for a channel, a photo or voice note in the feed, a chat folder, a
bot's Mini App: each is in the session's ``PlaybookPlan`` or not tried at all, and
each runs at most once a session (a search once per query). The same rule as the
rest of the playbook: every tap goes to a node the current dump showed, found by its
label or by the layout (``_selectors``); what the screen does not show is skipped
and named in the result. Settings screens open by deep link (``tg://settings/folders``,
``tg://resolve``), as a tapped link does.

An error dialog (too many attempts, too many communities) is closed with its OK
button, or BACK, and ends the session with what it did (``app_error``). Joining a
channel is muted afterwards with ``_MUTE_CHANCE``. In a Mini App only scrolls are
made: a blind tap in a third-party page could buy or send something.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from core.config import settings
from core.emulator import adb
from core.emulator._errors import EmulatorError
from core.emulator._selectors import (
    app_error,
    channel_key,
    channel_rows,
    photos,
    playable,
    rows_below,
    scenario_labels,
    screen_size,
    search_field,
    webview_shown,
)
from core.emulator.telegram_app import app_package
from core.emulator.ui_dump import UiNode, fields, find

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from core.emulator._input import HumanInputEngine
    from core.emulator.ui_dump import Screen
    from schemas.emulator import (
        EmulatorRefusalCode,
        PlaybookPlan,
        PlaybookResult,
        PlaybookSkip,
    )

_EMULATOR_TEXT_UNSUPPORTED: EmulatorRefusalCode = "emulator_text_unsupported"
_FOLDERS_URI = "tg://settings/folders"
_BOT_USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,31}")
# A search: results looked at, the feed of the channel opened read this many screens.
_RESULTS_WAIT = (1.5, 2.5)
_SEARCH_SCROLLS = (2, 4)
_PREFIX_MIN = 3
_MUTE_CHANCE = 0.65
# A photo is looked at this long, the next one swiped to with this chance; a note is
# played for this share of its length, never longer than a video note's minute. With
# both in the feed, a photo is picked with this chance.
_PHOTO_DWELL = (2.5, 5.0)
_NEXT_PHOTO_CHANCE = 0.4
_NOTE_SHARE = (0.9, 1.0)
_NOTE_MAX_SECONDS = 60.0
_PHOTO_CHANCE = 0.5
# A new folder takes this many chats.
_FOLDER_CHATS = (2, 3)
# A Mini App is used this long, in this many scrolls; it opens only with that much left.
_WEBAPP_SECONDS = (30.0, 45.0)
_WEBAPP_STEPS = (4, 6)
_WEBAPP_TRIES = 5
# Gesture spots as shares of the screen: a horizontal flick across the middle, a
# photo dragged down to close, a page scrolled from below the middle.
_FLICK_FROM, _FLICK_TO, _FLICK_Y = 0.8, 0.2, 0.5
_DISMISS_FROM, _DISMISS_TO = 0.45, 0.8
_SCROLL_START, _SCROLL_LENGTH = 0.65, (0.2, 0.35)


async def _package(serial: str) -> str:
    """The phone's own Telegram package, for a link opened in it."""
    return await app_package(serial) or settings.emulator.telegram_package


class ScenarioSteps:
    """The scenarios, mixed into ``TelegramMobilePlaybook`` (which owns time and screens)."""

    engine: HumanInputEngine
    result: PlaybookResult
    plan: PlaybookPlan
    _chats: list[UiNode] | None
    _read: set[str]
    _halted: bool
    _queries: list[str]
    _folders_left: bool
    _webapp_left: bool

    if TYPE_CHECKING:

        def _seconds_left(self) -> float: ...
        def _time_left(self) -> bool: ...
        async def _screen(self, serial: str) -> tuple[Screen, list[UiNode]]: ...
        async def _chat_list(self, serial: str) -> list[UiNode]: ...
        async def return_to_chats(self, serial: str) -> None: ...
        async def read_channel_feed(
            self, serial: str, scroll_count: int, *, react: bool = False
        ) -> None: ...

    def _start_scenarios(self, plan: PlaybookPlan) -> None:
        self._halted = False
        self._queries = list(plan.search_queries)
        self._folders_left = plan.folders
        self._webapp_left = plan.webapp_bot is not None

    def _skip(self, reason: PlaybookSkip) -> None:
        if reason not in self.result.skipped:
            self.result.skipped.append(reason)

    def _halt(self, reason: PlaybookSkip) -> None:
        self._skip(reason)
        self._halted = True

    async def _settle(self) -> None:
        """One poll interval for a tap's screen to come up."""
        await asyncio.sleep(settings.emulator.poll_seconds)

    async def _tap(self, serial: str, node: UiNode) -> None:
        self._chats = None
        await adb.human_tap(serial, node, engine=self.engine)

    async def _dismiss_error(self, serial: str, nodes: list[UiNode]) -> bool:
        """Close an error dialog the screen shows and end the session; ``False`` if none."""
        if not app_error(nodes):
            return False
        self._halt("app_error")
        ok = find(nodes, scenario_labels().ok)
        if ok is not None:
            await self._tap(serial, ok)
        else:
            await adb.press(serial, adb.KEYCODE_BACK)
        await self._settle()
        return True

    def _scenarios(self) -> list[Callable[[str], Awaitable[bool]]]:
        """The scenarios this session still has in its plan (and the time for)."""
        found: list[Callable[[str], Awaitable[bool]]] = []
        if self._queries:
            found.append(self._search)
        if self._folders_left:
            found.append(self._folders)
        if self._webapp_left and self._seconds_left() >= _WEBAPP_SECONDS[1]:
            found.append(self._webapp)
        return found

    async def _search(self, serial: str) -> bool:
        query = self._queries.pop()
        cut = self.engine.rng.randint(min(_PREFIX_MIN, len(query)), len(query))
        return await self.search_and_discover_channel(serial, query[:cut])

    async def _folders(self, serial: str) -> bool:
        self._folders_left = False
        return await self.organize_chat_folders(serial, self.plan.folder_name)

    async def _webapp(self, serial: str) -> bool:
        self._webapp_left = False
        bot = self.plan.webapp_bot
        return bot is not None and await self.interact_with_webapp(serial, bot)

    async def search_and_discover_channel(self, serial: str, search_prefix: str) -> bool:
        """Search for ``search_prefix``, open a channel it finds, read it; join when planned.

        Typed at a person's pace; the results are looked at and scrolled before one
        is opened, and the channel read a few screens. A joined channel is muted
        with ``_MUTE_CHANCE``.
        """
        rng = self.engine.rng
        button = find(await self._chat_list(serial), scenario_labels().search)
        if button is None:
            self._skip("no_search")
            return False
        await self._tap(serial, button)
        await self._settle()
        _screen, nodes = await self._screen(serial)
        box = search_field(nodes)
        if box is None:
            self._skip("no_search")
            return False
        await adb.human_type(serial, search_prefix, engine=self.engine)
        self.result.searches_done += 1
        await asyncio.sleep(rng.uniform(*_RESULTS_WAIT))
        start, end = self.engine.sample_scroll(
            screen_size(nodes), _SCROLL_START, _SCROLL_LENGTH, downward=False
        )
        await adb.human_swipe(serial, start, end, engine=self.engine)
        _screen, nodes = await self._screen(serial)
        results = [row for row in channel_rows(nodes) if channel_key(row) not in self._read]
        if not results:
            self._skip("no_channel")
            return True
        row = rng.choice(results)
        self._read.add(channel_key(row))
        await self._tap(serial, row)
        self.result.channels_read += 1
        await self._settle()
        await self.read_channel_feed(serial, rng.randint(*_SEARCH_SCROLLS))
        if self.plan.join_channels and self._time_left():
            await self._join(serial)
        return True

    async def _join(self, serial: str) -> None:
        labels = scenario_labels()
        _screen, nodes = await self._screen(serial)
        join = find(nodes, labels.join)
        if join is None:
            return
        await self._tap(serial, join)
        await self._settle()
        _screen, nodes = await self._screen(serial)
        if await self._dismiss_error(serial, nodes) or find(nodes, labels.join) is not None:
            return
        self.result.channels_joined += 1
        mute = find(nodes, labels.mute)
        if mute is not None and self.engine.rng.random() < _MUTE_CHANCE:
            await self._tap(serial, mute)

    async def consume_feed_media(self, serial: str) -> None:
        """Look at a photo of the open feed full screen, or play a note the plan allows."""
        _screen, nodes = await self._screen(serial)
        pictures, notes = photos(nodes), playable(nodes) if self.plan.notes else []
        if not pictures and not notes:
            self._skip("no_media")
            return
        rng = self.engine.rng
        if pictures and (not notes or rng.random() < _PHOTO_CHANCE):
            await self._view_photo(serial, rng.choice(pictures), screen_size(nodes))
        else:
            button, seconds = rng.choice(notes)
            await self._tap(serial, button)
            listen = min(seconds * rng.uniform(*_NOTE_SHARE), _NOTE_MAX_SECONDS)
            await asyncio.sleep(max(0.0, min(listen, self._seconds_left())))
            self.result.notes_played += 1
        self.result.media_consumed += 1

    async def _view_photo(self, serial: str, photo: UiNode, size: tuple[int, int]) -> None:
        """Open it, look, maybe flick to the next one, drag the viewer down to close it."""
        rng = self.engine.rng
        width, height = size
        await self._tap(serial, photo)
        await asyncio.sleep(rng.uniform(*_PHOTO_DWELL))
        if rng.random() < _NEXT_PHOTO_CHANCE and self._time_left():
            row = round(height * _FLICK_Y)
            flick = (round(width * _FLICK_FROM), row), (round(width * _FLICK_TO), row)
            await adb.human_fling(serial, *flick, engine=self.engine)
            await asyncio.sleep(rng.uniform(*_PHOTO_DWELL))
        middle = width // 2
        await adb.human_swipe(
            serial,
            (middle, round(height * _DISMISS_FROM)),
            (middle, round(height * _DISMISS_TO)),
            engine=self.engine,
        )
        await self._settle()

    async def organize_chat_folders(self, serial: str, folder_name: str | None = None) -> bool:
        """Add a recommended folder, or create ``folder_name`` with a few chats; swipe the tabs."""
        labels = scenario_labels()
        self._chats = None
        await adb.open_uri(serial, _FOLDERS_URI, await _package(serial))
        await self._settle()
        _screen, nodes = await self._screen(serial)
        if await self._dismiss_error(serial, nodes):
            return False
        recommended = find(nodes, labels.folders_add)
        create = find(nodes, labels.folders_create)
        if recommended is not None:
            await self._tap(serial, recommended)
        elif (
            create is None
            or not folder_name
            or not await self._create_folder(serial, create, folder_name)
        ):
            self._skip("no_folder")
            return False
        self.result.folders_created += 1
        await self.return_to_chats(serial)
        await self._swipe_folder_tabs(serial)
        return True

    async def _create_folder(self, serial: str, create: UiNode, name: str) -> bool:
        labels = scenario_labels()
        await self._tap(serial, create)
        await self._settle()
        _screen, nodes = await self._screen(serial)
        boxes, add_chats = fields(nodes), find(nodes, labels.folder_add_chats)
        if not boxes or add_chats is None:
            return False
        await self._tap(serial, boxes[0])
        await adb.human_type(serial, name, engine=self.engine)
        await self._tap(serial, add_chats)
        await self._settle()
        chosen: set[str] = set()
        for _ in range(self.engine.rng.randint(*_FOLDER_CHATS)):
            _screen, nodes = await self._screen(serial)
            header = find(nodes, labels.folder_chats_header)
            rows = (
                [row for row in rows_below(nodes, header) if row.text not in chosen]
                if (header is not None)
                else []
            )
            if not rows:
                break
            row = self.engine.rng.choice(rows)
            chosen.add(row.text)
            await self._tap(serial, row)
        _screen, nodes = await self._screen(serial)
        done = find(nodes, labels.save)
        if not chosen or done is None:
            return False
        await self._tap(serial, done)
        await self._settle()
        _screen, nodes = await self._screen(serial)
        save = find(nodes, labels.save)
        if save is None:
            return False
        await self._tap(serial, save)
        await self._settle()
        return True

    async def _swipe_folder_tabs(self, serial: str) -> None:
        """Flick across the chat list to the next folder and back again.

        One tab each way: the new folder makes a second tab, and a flick past the last
        one could land on a chat row's swipe action (archive) instead.
        """
        width, height = screen_size(await self._chat_list(serial))
        self._chats = None
        row = round(height * _FLICK_Y)
        forward = (round(width * _FLICK_FROM), row), (round(width * _FLICK_TO), row)
        for start, end in (forward, forward[::-1]):
            if not self._time_left():
                return
            await adb.human_fling(serial, start, end, engine=self.engine)
            pause, _back = self.engine.sample_reading_pause_sec()
            await asyncio.sleep(pause)

    async def interact_with_webapp(self, serial: str, bot_username: str) -> bool:
        """Open ``bot_username``'s Mini App, scroll through it for a while, close it."""
        if _BOT_USERNAME.fullmatch(bot_username) is None:
            raise EmulatorError(_EMULATOR_TEXT_UNSUPPORTED)
        self._chats = None
        uri = f"tg://resolve?domain={bot_username}&startapp"
        await adb.open_uri(serial, uri, await _package(serial))
        nodes: list[UiNode] = []
        for _ in range(_WEBAPP_TRIES):
            await self._settle()
            _screen, nodes = await self._screen(serial)
            if webview_shown(nodes) or await self._dismiss_error(serial, nodes):
                break
            launch = find(nodes, scenario_labels().launch)
            if launch is not None:
                await self._tap(serial, launch)
        if self._halted or not webview_shown(nodes):
            self._skip("no_webapp")
            return False
        self.result.webapps_opened += 1
        rng = self.engine.rng
        steps = rng.randint(*_WEBAPP_STEPS)
        pause = min(rng.uniform(*_WEBAPP_SECONDS), self._seconds_left()) / steps
        for _ in range(steps):
            await asyncio.sleep(max(0.0, pause))
            if not self._time_left():
                break
            start, end = self.engine.sample_scroll(
                screen_size(nodes), _SCROLL_START, _SCROLL_LENGTH, downward=False
            )
            await adb.human_swipe(serial, start, end, engine=self.engine)
        await adb.press(serial, adb.KEYCODE_BACK)
        await self._settle()
        _screen, nodes = await self._screen(serial)
        anyway = find(nodes, scenario_labels().close_anyway)
        if anyway is not None:
            await self._tap(serial, anyway)
        return True
