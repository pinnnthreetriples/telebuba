"""Activity sessions: what is tapped, swiped and skipped on a scripted Telegram screen.

``read_screen`` is replaced by a fake phone whose screen follows the gestures the
console records (a tap on a channel row opens the channel, BACK returns to the chat
list), and time is a fake clock that only ``asyncio.sleep`` moves.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

import pytest

from core.emulator import playbook
from core.emulator._errors import EmulatorError
from core.emulator._input import HumanInputEngine
from core.emulator.playbook import TelegramMobilePlaybook, channel_rows, posts, story_avatars
from core.emulator.ui_dump import UiNode
from schemas.emulator import PlaybookPlan

if TYPE_CHECKING:
    from core.emulator.ui_dump import Screen
    from tests.core.emulator.conftest import Console

SERIAL = "127.0.0.1:5601"
NO_REACTIONS = PlaybookPlan(reaction_probability=0.0)
WIDTH, HEIGHT = 1080, 2400


def _node(
    text: str = "", description: str = "", bounds: tuple[int, int, int, int] = (0, 0, 1, 1)
) -> UiNode:
    return UiNode(text, description, "android.view.ViewGroup", password=False, bounds=bounds)


ROOT = _node(bounds=(0, 0, WIDTH, HEIGHT))
MENU = _node(description="Open navigation menu", bounds=(0, 60, 140, 200))
CHANNEL_ROW = _node(text="Channel. Tech News. 3 unread messages", bounds=(0, 400, WIDTH, 560))
GROUP_ROW = _node(text="Group. Neighbours", bounds=(0, 560, WIDTH, 720))
STORY = _node(description="Stories by Anna", bounds=(180, 220, 320, 360))
OWN_STORY = _node(description="My story", bounds=(20, 220, 160, 360))
POST = _node(text="A long post about phones", bounds=(40, 700, 1040, 1200))


class _Phone:
    """The app's current screen, moved by the gestures the console sees."""

    def __init__(self, console: Console, chats: list[UiNode]) -> None:
        self.chats = chats
        self.screen: Screen = "chats"
        self.inside: list[UiNode] = [ROOT, POST]
        console.answer = self.on_command

    def on_command(self, argv: list[str]) -> str:
        device = argv[argv.index("shell") + 1 :]
        if device[:3] == ["input", "keyevent", "4"]:
            self.screen = "chats"
        elif device[:2] == ["input", "swipe"] and self.screen == "chats":
            x, y = int(device[2]), int(device[3])
            for node, opens in ((CHANNEL_ROW, "channel"), (STORY, "story")):
                left, top, right, bottom = node.bounds
                if node in self.chats and left <= x < right and top <= y < bottom:
                    self.screen = "unknown"
                    self.inside = [ROOT, POST] if opens == "channel" else [ROOT]
        return ""

    async def read_screen(self, _serial: str) -> tuple[Screen, list[UiNode]]:
        if self.screen == "chats":
            return "chats", [ROOT, MENU, *self.chats]
        return self.screen, self.inside


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(playbook.asyncio, "sleep", fake.sleep)
    monkeypatch.setattr(playbook, "_now", lambda: fake.now)
    return fake


def _phone(monkeypatch: pytest.MonkeyPatch, console: Console, chats: list[UiNode]) -> _Phone:
    phone = _Phone(console, chats)
    monkeypatch.setattr(playbook, "read_screen", phone.read_screen)
    return phone


class _Engine(HumanInputEngine):
    """Seeded, with a fixed 2 s reading pause."""

    def __init__(self, *, scroll_back: bool) -> None:
        super().__init__(random.Random(7))  # noqa: S311 - reproducible test
        self.scroll_back = scroll_back

    def sample_reading_pause_sec(self) -> tuple[float, bool]:
        return 2.0, self.scroll_back


# The fixed 2 s pause, stretched for POST's text (``_reading_pause``).
POST_PAUSE = 2.0 * (playbook._TEXT_FACTOR_MIN + len(POST.text) / playbook._CHARS_PER_FACTOR)


def _playbook(*, scroll_back: bool = False) -> TelegramMobilePlaybook:
    return TelegramMobilePlaybook(_Engine(scroll_back=scroll_back))


def _opened_in_channel(phone: _Phone) -> None:
    phone.screen = "unknown"
    phone.inside = [ROOT, POST]


def test_selectors_find_channels_others_stories_and_posts() -> None:
    chats = [ROOT, MENU, CHANNEL_ROW, GROUP_ROW, STORY, OWN_STORY]
    header = _node(text="Tech News", bounds=(0, 60, WIDTH, 260))
    short = _node(text="12:01", bounds=(900, 1300, 1000, 1340))

    assert channel_rows(chats) == [CHANNEL_ROW]
    assert story_avatars(chats) == [STORY], "the own-story button opens the camera"
    assert posts([ROOT, header, short, POST]) == [POST]


@pytest.mark.asyncio
async def test_a_slow_read_drags_the_bezier_curve_down_the_history(
    console: Console, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    monkeypatch.setattr(playbook, "_FLING_CHANCE", 0.0)
    _opened_in_channel(_phone(monkeypatch, console, [CHANNEL_ROW]))
    session = _playbook()

    await session.read_channel_feed(SERIAL, 3)

    calls = console.shell_calls()
    assert len(calls) == 3
    for call in calls:
        down, up = call[:5], call[-5:]
        assert down[:3] == ["input", "motionevent", "DOWN"]
        assert up[:3] == ["input", "motionevent", "UP"]
        assert int(down[4]) < int(up[4]), "the finger moves down: older posts"
    assert session.result.scrolls == 3
    assert clock.slept == [POST_PAUSE] * 3


@pytest.mark.asyncio
async def test_a_flick_is_a_short_input_swipe(
    console: Console, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    monkeypatch.setattr(playbook, "_FLING_CHANCE", 1.0)
    _opened_in_channel(_phone(monkeypatch, console, [CHANNEL_ROW]))

    await _playbook().read_channel_feed(SERIAL, 1)

    [call] = console.shell_calls()
    assert call[:2] == ["input", "swipe"]
    assert 120 <= int(call[6]) <= 180
    assert int(call[3]) < int(call[5])
    assert clock.slept == [POST_PAUSE]


@pytest.mark.asyncio
async def test_a_scroll_back_rereads_with_a_short_upward_drag(
    console: Console, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    monkeypatch.setattr(playbook, "_FLING_CHANCE", 0.0)
    _opened_in_channel(_phone(monkeypatch, console, [CHANNEL_ROW]))

    await _playbook(scroll_back=True).read_channel_feed(SERIAL, 1)

    read, back = console.shell_calls()
    assert int(read[4]) < int(read[-1])
    assert int(back[4]) > int(back[-1]), "the finger moves up again"
    assert clock.slept == [POST_PAUSE]


def _ends(call: list[str]) -> tuple[tuple[int, int], tuple[int, int]]:
    """A ``human_swipe`` call's DOWN and UP points."""
    return (int(call[3]), int(call[4])), (int(call[-2]), int(call[-1]))


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_reads_start_at_a_right_thumbs_spot_never_the_same_pixel(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(playbook, "_FLING_CHANCE", 0.0)
    _opened_in_channel(_phone(monkeypatch, console, [CHANNEL_ROW]))

    await _playbook().read_channel_feed(SERIAL, 8)

    drags = [_ends(call) for call in console.shell_calls()]
    assert len({start for start, _end in drags}) == len(drags), "no two DOWNs on one pixel"
    for (x, y), (end_x, end_y) in drags:
        assert 0.48 * WIDTH - 1 <= x <= 0.64 * WIDTH + 1
        assert 0.26 * HEIGHT - 1 <= y <= 0.34 * HEIGHT + 1
        assert 0.3 * HEIGHT - 1 <= end_y - y <= 0.5 * HEIGHT + 1
        assert abs(end_x - x) <= 0.03 * WIDTH + 1
    assert sum(x for (x, _y), _end in drags) / len(drags) > WIDTH / 2, "a right thumb"


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_a_scroll_back_starts_somewhere_new_too(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(playbook, "_FLING_CHANCE", 0.0)
    _opened_in_channel(_phone(monkeypatch, console, [CHANNEL_ROW]))

    await _playbook(scroll_back=True).read_channel_feed(SERIAL, 4)

    backs = [_ends(call) for call in console.shell_calls()[1::2]]
    assert len({start for start, _end in backs}) == len(backs)
    for (x, y), (_end_x, end_y) in backs:
        assert 0.48 * WIDTH - 1 <= x <= 0.64 * WIDTH + 1
        assert 0.56 * HEIGHT - 1 <= y <= 0.64 * HEIGHT + 1
        assert 0.15 * HEIGHT - 1 <= y - end_y <= 0.25 * HEIGHT + 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_one_seed_scrolls_the_same_way_twice(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    _opened_in_channel(_phone(monkeypatch, console, [CHANNEL_ROW]))
    await _playbook().read_channel_feed(SERIAL, 3)
    first = console.shell_calls()
    console.calls.clear()

    await _playbook().read_channel_feed(SERIAL, 3)

    assert console.shell_calls() == first


@pytest.mark.asyncio
async def test_a_reaction_double_taps_a_post_on_screen(
    console: Console, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    _opened_in_channel(_phone(monkeypatch, console, [CHANNEL_ROW]))
    session = _playbook()

    await session.read_channel_feed(SERIAL, 1, react=True)

    tap = console.shell_calls()[0]
    assert tap[:2] == ["input", "tap"]
    assert tap[4:] == [";", "input", "tap", tap[2], tap[3]], "one point, tapped twice"
    left, top, right, bottom = POST.bounds
    assert left <= int(tap[2]) < right
    assert top <= int(tap[3]) < bottom
    assert session.result.reactions == 1
    assert clock.slept


@pytest.mark.asyncio
async def test_no_post_on_screen_is_skipped_not_guessed(
    console: Console, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    phone = _phone(monkeypatch, console, [CHANNEL_ROW])
    _opened_in_channel(phone)
    phone.inside = [ROOT]
    session = _playbook()

    await session.read_channel_feed(SERIAL, 1, react=True)

    assert not any(call[:2] == ["input", "tap"] for call in console.shell_calls())
    assert session.result.skipped == ["no_post"]
    assert clock.slept


@pytest.mark.asyncio
async def test_reading_stops_when_the_chat_list_shows(
    console: Console, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    _phone(monkeypatch, console, [CHANNEL_ROW])
    session = _playbook()

    await session.read_channel_feed(SERIAL, 3)

    assert console.shell_calls() == [], "nothing is swiped on the chat list"
    assert session.result.scrolls == 0
    assert clock.slept == []


@pytest.mark.asyncio
async def test_stories_are_opened_watched_and_tapped_on(
    console: Console, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    _phone(monkeypatch, console, [OWN_STORY, STORY])
    session = _playbook()

    await session.watch_stories(SERIAL, max_stories=1)

    opened, next_story = console.shell_calls()
    left, top, right, bottom = STORY.bounds
    assert left <= int(opened[2]) < right
    assert top <= int(opened[3]) < bottom
    assert int(next_story[2]) >= WIDTH * 0.7, "the viewer's right third"
    assert session.result.stories_watched == 1
    assert 2.0 <= clock.slept[0] <= 9.0


@pytest.mark.asyncio
async def test_without_others_stories_nothing_is_tapped(
    console: Console, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    _phone(monkeypatch, console, [OWN_STORY])
    session = _playbook()

    await session.watch_stories(SERIAL)

    assert console.shell_calls() == []
    assert session.result.skipped == ["no_stories"]
    assert clock.slept == []


@pytest.mark.asyncio
async def test_stories_stop_when_the_viewer_closes(
    console: Console, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    phone = _phone(monkeypatch, console, [STORY])
    session = _playbook()

    async def _viewer_closes(seconds: float) -> None:
        clock.slept.append(seconds)
        phone.screen = "chats"

    monkeypatch.setattr(playbook.asyncio, "sleep", _viewer_closes)
    await session.watch_stories(SERIAL, max_stories=5)

    assert len(console.shell_calls()) == 1, "only the avatar; no tap on the chat list"
    assert session.result.stories_watched == 0


@pytest.mark.asyncio
async def test_back_is_pressed_until_the_chat_list_shows(
    console: Console, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    _opened_in_channel(_phone(monkeypatch, console, [CHANNEL_ROW]))

    await _playbook().return_to_chats(SERIAL)

    assert console.shell_calls() == [["input", "keyevent", "4"]]
    assert len(clock.slept) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("screen", ["intro", "phone", "code", "password", "confirm"])
async def test_a_sign_in_screen_is_never_backed_out_of(
    console: Console, monkeypatch: pytest.MonkeyPatch, clock: _Clock, screen: Screen
) -> None:
    _phone(monkeypatch, console, []).screen = screen

    with pytest.raises(EmulatorError) as refused:
        await _playbook().return_to_chats(SERIAL)

    assert refused.value.code == "emulator_not_signed_in"
    assert console.shell_calls() == []
    assert clock.slept == []


@pytest.mark.asyncio
async def test_a_screen_back_never_leaves_is_refused(
    console: Console, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    phone = _phone(monkeypatch, console, [])

    async def _stuck(_serial: str) -> tuple[Screen, list[UiNode]]:
        return "unknown", [ROOT]

    monkeypatch.setattr(playbook, "read_screen", _stuck)
    del phone

    with pytest.raises(EmulatorError) as refused:
        await _playbook().return_to_chats(SERIAL)

    assert refused.value.code == "emulator_field_missing"
    assert len(console.shell_calls()) == playbook._MAX_BACKS + 1
    assert len(clock.slept) == playbook._MAX_BACKS + 1


@pytest.mark.asyncio
async def test_an_empty_dump_is_refused_before_any_gesture(
    console: Console, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    async def _empty(_serial: str) -> tuple[Screen, list[UiNode]]:
        return "unknown", []

    monkeypatch.setattr(playbook, "read_screen", _empty)

    with pytest.raises(EmulatorError) as refused:
        await _playbook().read_channel_feed(SERIAL, 1)

    assert refused.value.code == "emulator_field_missing"
    assert console.shell_calls() == []
    assert clock.slept == []


@pytest.mark.asyncio
async def test_a_session_reads_each_channel_once_and_ends_on_chats(
    console: Console, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    monkeypatch.setattr(playbook, "_STORIES_SHARE", 0.0)
    monkeypatch.setattr(playbook, "_FLING_CHANCE", 0.0)
    phone = _phone(monkeypatch, console, [CHANNEL_ROW])

    result = await _playbook().run_session(SERIAL, 600, NO_REACTIONS)

    assert result.channels_read == 1, "the one channel is not read again"
    assert result.scrolls >= 3
    assert clock.now < 600
    assert phone.screen == "chats"
    assert result.skipped == ["no_channel", "no_stories"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("clock")
async def test_a_session_falls_back_to_the_other_activity(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(playbook, "_STORIES_SHARE", 1.0)
    _phone(monkeypatch, console, [CHANNEL_ROW])

    result = await _playbook().run_session(SERIAL, 600, NO_REACTIONS)

    assert result.channels_read == 1
    assert result.skipped == ["no_stories", "no_channel"], "stories are tried once"


@pytest.mark.asyncio
async def test_a_session_with_nothing_to_do_ends_at_once(
    console: Console, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    monkeypatch.setattr(playbook, "_STORIES_SHARE", 0.0)
    _phone(monkeypatch, console, [GROUP_ROW, OWN_STORY])

    result = await _playbook().run_session(SERIAL, 600)

    assert result.skipped == ["no_channel", "no_stories"]
    assert console.shell_calls() == []
    assert clock.slept == []


CHATS_XML = (
    "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">"
    '<node text="" content-desc="" class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">'
    '<node text="" content-desc="Open navigation menu" class="android.widget.ImageView"'
    ' bounds="[0,60][140,200]" />'
    '<node text="" content-desc="My story" class="android.view.View" bounds="[20,220][160,360]" />'
    '<node text="" content-desc="Stories by Anna" class="android.view.View"'
    ' bounds="[180,220][320,360]" />'
    '<node text="Channel. Tech News. 3 unread messages" content-desc=""'
    ' class="android.view.ViewGroup" bounds="[0,400][1080,560]" />'
    "</node></hierarchy>"
)
CHANNEL_XML = (
    "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">"
    '<node text="" content-desc="" class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">'
    '<node text="Tech News" content-desc="" class="android.widget.TextView"'
    ' bounds="[0,60][1080,260]" />'
    '<node text="A long post about phones" content-desc="" class="android.view.ViewGroup"'
    ' bounds="[40,700][1040,1200]" />'
    "</node></hierarchy>"
)


def _serve_dumps(console: Console) -> list[str]:
    """Answer the real ``uiautomator dump`` + ``cat``; return the screens served, in order.

    A tap on the channel row opens the channel; BACK returns to the chat list.
    """
    state = {"xml": CHANNEL_XML}
    served: list[str] = []

    def answer(argv: list[str]) -> str:
        device = argv[argv.index("shell") + 1 :]
        if "uiautomator" in device:
            served.append("chats" if state["xml"] is CHATS_XML else "channel")
            return f"UI hierchary dumped to: /sdcard/telebuba_ui.xml\n{state['xml']}"
        if device[:3] == ["input", "keyevent", "4"]:
            state["xml"] = CHATS_XML
        elif (
            device[:2] == ["input", "swipe"]
            and state["xml"] is CHATS_XML
            and 400 <= int(device[3]) < 560
        ):
            state["xml"] = CHANNEL_XML
        return ""

    console.answer = answer
    return served


def _gestures(console: Console) -> list[list[str]]:
    return [call for call in console.shell_calls() if call[0] == "input"]


@pytest.mark.asyncio
async def test_real_dumps_a_lost_channel_screen_backs_out_to_the_chat_list(
    console: Console, clock: _Clock
) -> None:
    served = _serve_dumps(console)

    await _playbook().return_to_chats(SERIAL)

    assert _gestures(console) == [["input", "keyevent", "4"]]
    assert served == ["channel", "chats"]
    assert len(clock.slept) == 1


@pytest.mark.asyncio
async def test_real_dumps_a_channel_is_opened_read_and_reacted_to(
    console: Console, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> None:
    monkeypatch.setattr(playbook, "_STORIES_SHARE", 0.0)
    monkeypatch.setattr(playbook, "_FLING_CHANCE", 0.0)
    monkeypatch.setattr(playbook, "_FEED_SCROLLS", (1, 1))
    _serve_dumps(console).clear()
    session = _playbook()

    result = await session.run_session(SERIAL, 1, PlaybookPlan(reaction_probability=1.0))

    gestures = _gestures(console)
    back, open_row, react, read, back_again = gestures
    assert back == ["input", "keyevent", "4"], "the session starts on the chat list"
    assert open_row[:2] == ["input", "swipe"]
    assert 400 <= int(open_row[3]) < 560, "the channel row the dump showed"
    assert react[:2] == ["input", "tap"]
    assert 700 <= int(react[3]) < 1200, "the post, not the header"
    assert read[:3] == ["input", "motionevent", "DOWN"]
    assert back_again == ["input", "keyevent", "4"]
    assert result.channels_read == 1
    assert result.reactions == 1
    assert clock.now >= 1


@pytest.mark.asyncio
async def test_real_dumps_stories_open_others_never_the_own_story(
    console: Console, clock: _Clock
) -> None:
    served = _serve_dumps(console)
    await _playbook().return_to_chats(SERIAL)
    served.clear()
    console.calls.clear()

    await _playbook().watch_stories(SERIAL, max_stories=1)

    opened = _gestures(console)[0]
    assert opened[:2] == ["input", "swipe"]
    assert 180 <= int(opened[2]) < 320, "Anna's story, not the own-story button"
    assert 220 <= int(opened[3]) < 360
    assert clock.slept
