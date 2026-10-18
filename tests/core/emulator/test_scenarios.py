"""The scenarios a session mixes in: search, media, folders, a Mini App, error dialogs.

A scripted app moves between named screens on the gestures the console records (a
tap inside a node, BACK, a deep link, typed text); time is the fake clock of
``test_playbook``, which only ``asyncio.sleep`` moves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from core.emulator import _scenarios, playbook
from core.emulator._errors import EmulatorError
from core.emulator.ui_dump import UiNode
from schemas.emulator import PlaybookPlan
from tests.core.emulator.test_playbook import (  # noqa: F401 - the clock fixture
    CHANNEL_ROW,
    MENU,
    POST,
    ROOT,
    SERIAL,
    WIDTH,
    _Clock,
    _node,
    _playbook,
    clock,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from core.emulator.ui_dump import Screen
    from tests.core.emulator.conftest import Console

Command = list[str]


def _text(text: str, bounds: tuple[int, int, int, int], cls: str = "TextView") -> UiNode:
    return UiNode(text, "", f"android.widget.{cls}", password=False, bounds=bounds)


SEARCH = _node(description="Поиск", bounds=(940, 60, 1080, 200))
SEARCH_BOX = _text("", (150, 60, 1000, 200), "EditText")
RESULT = _node(text="Channel. Space Daily", bounds=(0, 400, WIDTH, 560))
JOIN = _node(text="ПРИСОЕДИНИТЬСЯ", bounds=(0, 2250, WIDTH, 2400))
MUTE = _node(text="УБРАТЬ ЗВУК", bounds=(0, 2250, WIDTH, 2400))
PHOTO = _node(description="Фотография", bounds=(40, 700, 1040, 1300))
PLAY = _node(description="Reproducir", bounds=(60, 1400, 180, 1520))
DURATION = _text("0:20", (200, 1440, 320, 1480))
ERROR = _text("Too many attempts, please try again later.", (100, 1000, 980, 1200))
OK = _node(text="OK", bounds=(700, 1250, 980, 1350))
OFFLINE = _text("Ожидание сети...", (150, 60, 700, 200))


def tapped(node: UiNode) -> Callable[[Command], bool]:
    def check(device: Command) -> bool:
        if device[:2] != ["input", "swipe"]:
            return False
        left, top, right, bottom = node.bounds
        return left <= int(device[2]) < right and top <= int(device[3]) < bottom

    return check


def back(device: Command) -> bool:
    return device[:3] == ["input", "keyevent", "4"]


def linked(part: str) -> Callable[[Command], bool]:
    return lambda device: device[:2] == ["am", "start"] and part in " ".join(device)


class _App:
    """Named screens (``chats*`` is the chat list); a matching gesture moves on."""

    def __init__(self, console: Console, screens: dict[str, list[UiNode]], at: str) -> None:
        self.screens = screens
        self.at = at
        self.moves: list[tuple[str, Callable[[Command], bool], str]] = []
        self.seen: list[str] = []
        console.answer = self.on_command

    def move(self, start: str, when: Callable[[Command], bool], to: str) -> _App:
        self.moves.append((start, when, to))
        return self

    def on_command(self, argv: Command) -> str:
        device = argv[argv.index("shell") + 1 :]
        for start, when, to in self.moves:
            if start == self.at and when(device):
                self.at = to
                break
        return ""

    async def read_screen(self, _serial: str) -> tuple[Screen, list[UiNode]]:
        self.seen.append(self.at)
        return ("chats" if self.at.startswith("chats") else "unknown"), [
            ROOT,
            *self.screens[self.at],
        ]


def _app(
    monkeypatch: pytest.MonkeyPatch, console: Console, screens: dict[str, list[UiNode]], at: str
) -> _App:
    app = _App(console, screens, at)
    monkeypatch.setattr(playbook, "read_screen", app.read_screen)
    return app


def _typed(console: Console) -> str:
    return "".join(call[2] for call in console.shell_calls() if call[:2] == ["input", "text"])


def _searching_app(monkeypatch: pytest.MonkeyPatch, console: Console) -> _App:
    screens = {
        "chats": [MENU, SEARCH],
        "search": [SEARCH_BOX, RESULT],
        "channel": [POST, JOIN],
        "joined": [POST, MUTE],
    }
    return (
        _app(monkeypatch, console, screens, "chats")
        .move("chats", tapped(SEARCH), "search")
        .move("search", tapped(RESULT), "channel")
        .move("channel", tapped(JOIN), "joined")
        .move("channel", back, "chats")
        .move("joined", back, "chats")
    )


@pytest.mark.asyncio
async def test_a_search_types_opens_a_result_reads_it_and_joins_when_planned(
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
    clock: _Clock,  # noqa: F811
) -> None:
    monkeypatch.setattr(_scenarios, "_MUTE_CHANCE", 1.0)
    app = _searching_app(monkeypatch, console)
    session = _playbook()
    session.plan = PlaybookPlan(join_channels=True)

    assert await session.search_and_discover_channel(SERIAL, "spa")

    assert _typed(console) == "spa", "typed key by key, typos corrected"
    assert any(1.5 <= pause <= 2.5 for pause in clock.slept), "the results are looked at"
    assert app.at == "joined"
    assert any(tapped(MUTE)(call) for call in console.shell_calls()), "muted after joining"
    result = session.result
    assert (result.searches_done, result.channels_read, result.channels_joined) == (1, 1, 1)
    assert 2 <= result.scrolls <= 4


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_a_search_never_joins_without_the_plans_switch(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _searching_app(monkeypatch, console)
    session = _playbook()

    assert await session.search_and_discover_channel(SERIAL, "space")

    assert app.at == "channel"
    assert not any(tapped(JOIN)(call) for call in console.shell_calls())
    assert session.result.channels_joined == 0


@pytest.mark.asyncio
async def test_a_chat_list_without_a_search_button_is_skipped(
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
    clock: _Clock,  # noqa: F811
) -> None:
    _app(monkeypatch, console, {"chats": [MENU]}, "chats")
    session = _playbook()

    assert not await session.search_and_discover_channel(SERIAL, "space")

    assert console.shell_calls() == []
    assert session.result.skipped == ["no_search"]
    assert clock.slept == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_a_search_with_no_results_is_done_and_says_so(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app(monkeypatch, console, {"chats": [SEARCH], "search": [SEARCH_BOX]}, "chats").move(
        "chats", tapped(SEARCH), "search"
    )
    session = _playbook()

    assert await session.search_and_discover_channel(SERIAL, "zzz")

    assert session.result.searches_done == 1
    assert session.result.channels_read == 0
    assert session.result.skipped == ["no_channel"]


@pytest.mark.asyncio
async def test_a_photo_is_opened_looked_at_flicked_on_and_dragged_down(
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
    clock: _Clock,  # noqa: F811
) -> None:
    monkeypatch.setattr(_scenarios, "_NEXT_PHOTO_CHANCE", 1.0)
    app = _app(monkeypatch, console, {"feed": [PHOTO], "viewer": []}, "feed")
    app.move("feed", tapped(PHOTO), "viewer")
    session = _playbook()

    await session.consume_feed_media(SERIAL)

    _open, flick, *drag = console.shell_calls()
    assert app.at == "viewer"
    assert flick[:2] == ["input", "swipe"]
    assert int(flick[4]) < int(flick[2]), "a flick to the left: the next photo"
    assert 120 <= int(flick[6]) <= 180, "a short input swipe, not a drag"
    [dismiss] = drag
    assert dismiss[:3] == ["input", "motionevent", "DOWN"]
    assert int(dismiss[4]) < int(dismiss[-1]), "the viewer is dragged down to close"
    assert all(2.5 <= pause <= 5.0 for pause in clock.slept[:2])
    assert session.result.media_consumed == 1


@pytest.mark.asyncio
async def test_a_voice_note_is_played_for_most_of_its_length(
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
    clock: _Clock,  # noqa: F811
) -> None:
    _app(monkeypatch, console, {"feed": [PLAY, DURATION]}, "feed")
    session = _playbook()
    session._deadline = 600

    await session.consume_feed_media(SERIAL)

    [play] = console.shell_calls()
    assert tapped(PLAY)(play)
    assert 0.9 * 20 <= clock.slept[0] <= 20
    assert session.result.media_consumed == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_a_note_longer_than_the_session_is_cut_at_its_end(
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
    clock: _Clock,  # noqa: F811
) -> None:
    _app(monkeypatch, console, {"feed": [PLAY, DURATION]}, "feed")
    session = _playbook()
    session._deadline = 5

    await session.consume_feed_media(SERIAL)

    assert clock.slept == [5]


@pytest.mark.asyncio
async def test_a_feed_without_photos_or_notes_is_skipped(
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
    clock: _Clock,  # noqa: F811
) -> None:
    _app(monkeypatch, console, {"feed": [POST]}, "feed")
    session = _playbook()

    await session.consume_feed_media(SERIAL)

    assert console.shell_calls() == []
    assert session.result.skipped == ["no_media"]
    assert clock.slept == []


ADD = _node(text="Adicionar", bounds=(800, 900, 1040, 1000))
CREATE = _node(text="Створити нову папку", bounds=(0, 500, WIDTH, 620))
NAME_BOX = _text("", (40, 300, 1040, 400), "EditText")
ADD_CHATS = _node(text="Добавить чаты", bounds=(0, 700, WIDTH, 820))
HEADER = _text("Чати", (0, 300, WIDTH, 380))
PICKS = [_text(name, (0, 400 + 160 * i, WIDTH, 560 + 160 * i)) for i, name in enumerate("ABC")]
DONE = _node(description="Готово", bounds=(900, 2200, 1040, 2340))
SAVE = _node(text="Guardar", bounds=(860, 60, 1080, 200))


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_a_recommended_folder_is_added_then_the_tabs_are_swiped(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(monkeypatch, console, {"chats": [MENU], "folders": [ADD, CREATE]}, "chats")
    app.move("chats", linked("tg://settings/folders"), "folders").move("folders", back, "chats")
    session = _playbook()
    session._deadline = 600

    assert await session.organize_chat_folders(SERIAL)

    calls = console.shell_calls()
    assert calls[0][:2] == ["am", "start"]
    assert tapped(ADD)(calls[1]), "the recommended folder, not a new one"
    flicks = [call for call in calls[2:] if call[:2] == ["input", "swipe"]]
    assert flicks, "the folder tabs are swiped"
    assert all(abs(int(c[3]) - int(c[5])) < 20 for c in flicks), "sideways flicks"
    assert app.at == "chats"
    assert session.result.folders_created == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_a_new_folder_gets_its_name_and_a_few_chats(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    screens = {
        "chats": [MENU],
        "folders": [CREATE],
        "edit": [NAME_BOX, ADD_CHATS],
        "picker": [HEADER, *PICKS, DONE],
        "edited": [NAME_BOX, ADD_CHATS, SAVE],
    }
    app = (
        _app(monkeypatch, console, screens, "chats")
        .move("chats", linked("settings/folders"), "folders")
        .move("folders", tapped(CREATE), "edit")
        .move("edit", tapped(ADD_CHATS), "picker")
        .move("picker", tapped(DONE), "edited")
        .move("edited", tapped(SAVE), "folders")
        .move("folders", back, "chats")
    )
    session = _playbook()

    assert await session.organize_chat_folders(SERIAL, "Reading")

    assert _typed(console) == "Reading"
    picked = [n for n in PICKS if any(tapped(n)(c) for c in console.shell_calls())]
    assert 2 <= len(picked) <= 3
    assert app.at == "chats"
    assert session.result.folders_created == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_without_a_recommendation_or_a_name_no_folder_is_made(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(monkeypatch, console, {"chats": [MENU], "folders": [CREATE]}, "chats")
    app.move("chats", linked("settings/folders"), "folders")
    session = _playbook()

    assert not await session.organize_chat_folders(SERIAL)

    assert not any(tapped(CREATE)(call) for call in console.shell_calls())
    assert session.result.skipped == ["no_folder"]


LAUNCH = _node(text="Open App", bounds=(0, 2250, WIDTH, 2400))
WEBVIEW = _text("", (0, 200, WIDTH, 2400), "WebView")
CLOSE_ANYWAY = _node(text="Close anyway", bounds=(600, 1300, 1000, 1400))


@pytest.mark.asyncio
async def test_a_mini_app_is_launched_scrolled_and_closed(
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
    clock: _Clock,  # noqa: F811
) -> None:
    monkeypatch.setattr(playbook.settings.emulator, "telegram_package", "org.telegram.messenger")
    web = UiNode("", "", "android.webkit.WebView", password=False, bounds=WEBVIEW.bounds)
    screens = {"chats": [MENU], "bot": [LAUNCH], "app": [web], "closing": [CLOSE_ANYWAY]}
    app = (
        _app(monkeypatch, console, screens, "chats")
        .move("chats", linked("domain=demo_bot&startapp"), "bot")
        .move("bot", tapped(LAUNCH), "app")
        .move("app", back, "closing")
        .move("closing", tapped(CLOSE_ANYWAY), "chats")
    )
    session = _playbook()
    session._deadline = 600

    assert await session.interact_with_webapp(SERIAL, "demo_bot")

    calls = console.shell_calls()
    assert calls[0][-1] == "org.telegram.messenger"
    scrolls = [call for call in calls if call[:2] == ["input", "motionevent"]]
    assert 4 <= len(scrolls) <= 6
    assert 30 <= clock.now <= 46
    assert app.at == "chats"
    assert session.result.webapps_opened == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bot", ["bad bot", "x;rm", "a&b", "abc"])
async def test_a_bot_name_that_is_no_username_is_refused_before_anything(
    console: Console, bot: str
) -> None:
    with pytest.raises(EmulatorError) as refused:
        await _playbook().interact_with_webapp(SERIAL, bot)

    assert refused.value.code == "emulator_text_unsupported"
    assert console.shell_calls() == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_a_mini_app_that_never_opens_is_skipped(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app(monkeypatch, console, {"chats": [MENU]}, "chats")
    session = _playbook()

    assert not await session.interact_with_webapp(SERIAL, "demo_bot")

    assert session.result.skipped == ["no_webapp"]
    assert session.result.webapps_opened == 0


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_an_error_dialog_is_closed_and_the_session_ends_with_its_result(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(monkeypatch, console, {"chats": [MENU, CHANNEL_ROW, ERROR, OK]}, "chats")
    app.move("chats", tapped(OK), "chats_clean")
    app.screens["chats_clean"] = [MENU, CHANNEL_ROW]

    result = await _playbook().run_session(SERIAL, 600, PlaybookPlan(reaction_probability=0))

    [ok] = console.shell_calls()
    assert tapped(OK)(ok)
    assert result.skipped == ["app_error"]
    assert result.channels_read == 0, "nothing is opened once an error showed"


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_a_join_refused_by_a_limit_ends_the_session(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _searching_app(monkeypatch, console)
    app.screens["joined"] = [POST, ERROR]
    session = _playbook()
    session.plan = PlaybookPlan(join_channels=True)

    assert await session.search_and_discover_channel(SERIAL, "space")

    assert session._halted
    assert session.result.channels_joined == 0
    assert session.result.skipped == ["app_error"]


@pytest.mark.asyncio
async def test_an_app_that_stays_offline_ends_the_session(
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
    clock: _Clock,  # noqa: F811
) -> None:
    _app(monkeypatch, console, {"chats": [MENU, CHANNEL_ROW, OFFLINE]}, "chats")

    result = await _playbook().run_session(SERIAL, 600, PlaybookPlan(reaction_probability=0))

    assert result.skipped == ["app_offline"]
    assert result.channels_read == 0
    assert console.shell_calls() == []
    assert clock.now >= playbook.settings.emulator.ui_timeout_seconds


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_a_chat_list_that_comes_online_is_used(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads = iter([[MENU, OFFLINE], [MENU, CHANNEL_ROW]])

    async def comes_online(_serial: str) -> tuple[Screen, list[UiNode]]:
        return "chats", [ROOT, *next(reads)]

    monkeypatch.setattr(playbook, "read_screen", comes_online)
    session = _playbook()

    await session.return_to_chats(SERIAL)

    assert session._chats == [ROOT, MENU, CHANNEL_ROW]
    assert not session._halted
    assert console.shell_calls() == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_a_session_searches_when_there_is_nothing_else_to_do(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    _searching_app(monkeypatch, console)

    result = await _playbook().run_session(SERIAL, 600, PlaybookPlan(search_queries=["space"]))

    assert result.searches_done == 1, "each query once"
    assert 3 <= len(_typed(console)) <= 5
    assert result.channels_read == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_a_channel_read_ends_on_a_photo_when_media_is_planned(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(playbook, "_STORIES_SHARE", 0.0)
    monkeypatch.setattr(playbook, "_MEDIA_SHARE", 1.0)
    monkeypatch.setattr(playbook, "_FEED_SCROLLS", (1, 1))
    _app(monkeypatch, console, {"chats": [CHANNEL_ROW], "feed": [PHOTO]}, "chats").move(
        "chats", tapped(CHANNEL_ROW), "feed"
    ).move("feed", back, "chats")

    result = await _playbook().run_session(
        SERIAL, 600, PlaybookPlan(media=True, reaction_probability=0)
    )

    assert result.channels_read == 1
    assert result.media_consumed == 1


def test_account_changes_stay_off_in_a_default_plan() -> None:
    session = _playbook()
    session._start_scenarios(PlaybookPlan())

    assert session._scenarios() == []
    plan = PlaybookPlan()
    assert (plan.media, plan.join_channels, plan.folders, plan.webapp_bot) == (
        False,
        False,
        False,
        None,
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"search_queries": ["погода"]},
        {"search_queries": ["a"]},
        {"webapp_bot": "bad;bot"},
        {"folder_name": "a very long folder"},
    ],
)
def test_a_plan_refuses_what_input_text_or_a_deep_link_cannot_carry(fields: dict) -> None:
    with pytest.raises(ValidationError):
        PlaybookPlan(**fields)
