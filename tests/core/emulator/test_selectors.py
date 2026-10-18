"""Scenario targets on sample dumps, in the languages a container runs most.

Every label below is Telegram Android's own string (translations.telegram.org) in
English, Russian, Spanish, Brazilian Portuguese or Ukrainian, as the app shows it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.emulator import adb
from core.emulator._selectors import (
    app_error,
    channel_rows,
    duration_seconds,
    offline,
    photos,
    playable,
    rows_below,
    scenario_labels,
    search_field,
    webview_shown,
)
from core.emulator.ui_dump import find, parse_dump

if TYPE_CHECKING:
    from tests.core.emulator.conftest import Console

WIDTH, HEIGHT = 1080, 2400


def _dump(*nodes: str) -> str:
    return (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">"
        '<node text="" content-desc="" class="android.widget.FrameLayout"'
        f' bounds="[0,0][{WIDTH},{HEIGHT}]">{"".join(nodes)}</node></hierarchy>'
    )


def _node(
    bounds: str, *, text: str = "", desc: str = "", cls: str = "android.widget.TextView"
) -> str:
    return f'<node text="{text}" content-desc="{desc}" class="{cls}" bounds="{bounds}" />'


@pytest.mark.parametrize(
    ("search", "join", "mute"),
    [
        ("Search", "JOIN", "MUTE"),
        ("Поиск", "ПРИСОЕДИНИТЬСЯ", "УБРАТЬ ЗВУК"),
        ("Buscar", "UNIRME", "SILENCIAR"),
        ("Buscar", "ENTRAR", "SILENCIAR"),
        ("Пошук", "ПРИЄДНАТИСЯ", "НЕ СПОВІЩАТИ"),
    ],
)
def test_search_join_and_mute_are_found_in_every_language(
    search: str, join: str, mute: str
) -> None:
    labels = scenario_labels()
    chats = parse_dump(_dump(_node("[940,60][1080,200]", desc=search)))
    feed = parse_dump(
        _dump(_node("[0,2250][1080,2400]", text=join), _node("[0,60][1080,200]", text="News"))
    )

    assert find(chats, labels.search) is not None
    assert find(feed, labels.join) is not None
    assert find(parse_dump(_dump(_node("[0,2250][1080,2400]", text=mute))), labels.mute)
    assert find(feed, labels.mute) is None


def test_the_ukrainian_mute_is_telegrams_own_string_not_a_transliteration() -> None:
    assert "не сповіщати" in scenario_labels().mute
    assert "vimknuty zvuk" not in scenario_labels().mute


@pytest.mark.parametrize(
    "text",
    [
        "Too many attempts, please try again later.",
        "Лимит попыток превышен. Попробуйте позже.",
        "Demasiadas comunidades",
        "Muitas tentativas. Por favor, tente depois.",
        "Забагато спільнот",
        "Too many attempts, please try again in 5 minutes",
    ],
)
def test_an_error_dialog_is_recognised(text: str) -> None:
    dialog = parse_dump(_dump(_node("[100,1000][980,1200]", text=text)))

    assert app_error(dialog)


def test_a_post_saying_the_same_is_no_error_dialog() -> None:
    post = parse_dump(
        _dump(
            _node(
                "[40,700][1040,1200]",
                text="Too many communities in one feed",
                cls="android.view.ViewGroup",
            )
        )
    )

    assert not app_error(post)


@pytest.mark.parametrize(
    "title", ["Waiting for network...", "Соединение...", "Esperando red…", "Очікування мережі..."]
)
def test_a_chat_list_waiting_for_the_network_is_offline(title: str) -> None:
    assert offline(parse_dump(_dump(_node("[150,60][700,200]", text=title))))
    assert not offline(parse_dump(_dump(_node("[150,60][700,200]", text="Telegram"))))


def test_the_search_field_is_the_editable_box_at_the_top() -> None:
    box = "[150,60][1000,200]"
    nodes = parse_dump(
        _dump(
            _node(box, cls="android.widget.EditText"),
            _node("[0,2200][1080,2350]", cls="android.widget.EditText"),
        )
    )
    low_only = parse_dump(_dump(_node("[0,2200][1080,2350]", cls="android.widget.EditText")))

    field = search_field(nodes)
    assert field is not None
    assert field.bounds == (150, 60, 1000, 200)
    assert search_field(low_only) is None, "a chat's input box is no search field"
    assert search_field([]) is None


@pytest.mark.parametrize("word", ["Photo", "Фотография", "Foto", "Фото"])
def test_a_tall_photo_message_is_found_by_its_label(word: str) -> None:
    nodes = parse_dump(
        _dump(
            _node("[40,700][1040,1300]", desc=f"{word}, Sunset at sea", cls="android.view.View"),
            _node("[40,1400][1040,1440]", desc=word, cls="android.view.View"),
            _node("[0,60][1080,200]", text="Photo club"),
        )
    )

    [photo] = photos(nodes)
    assert photo.bounds == (40, 700, 1040, 1300), "not the thin line, not the header"
    assert photos([]) == []


@pytest.mark.parametrize(
    "play", ["Play", "Воспроизвести", "Reproducir", "Reproduzir", "Відтворити"]
)
def test_a_play_button_counts_with_the_duration_on_its_row(play: str) -> None:
    nodes = parse_dump(
        _dump(
            _node("[60,900][180,1020]", desc=play, cls="android.view.View"),
            _node("[200,960][320,1000]", text="0:42"),
            _node("[60,1500][180,1620]", desc=play, cls="android.view.View"),
        )
    )

    [(button, seconds)] = playable(nodes)
    assert button.bounds == (60, 900, 180, 1020)
    assert seconds == 42, "the second button has no duration: it is no note"


def test_the_duration_next_to_the_button_wins_over_the_sending_time() -> None:
    nodes = parse_dump(
        _dump(
            _node("[900,960][1040,1000]", text="12:05"),
            _node("[60,900][180,1020]", desc="Play", cls="android.view.View"),
            _node("[200,960][320,1000]", text="0:42"),
        )
    )

    assert playable(nodes)[0][1] == 42


@pytest.mark.parametrize("text", ["Photography tips for the weekend", "Фотограф года"])
def test_a_tall_post_merely_mentioning_photos_is_no_photo(text: str) -> None:
    nodes = parse_dump(_dump(_node("[40,700][1040,1300]", text=text)))

    assert photos(nodes) == []


@pytest.mark.parametrize("word", ["start", "iniciar", "starten", "begin"])
def test_a_mini_app_is_never_launched_with_the_bots_start_button(word: str) -> None:
    assert word not in scenario_labels().launch
    assert "open app" in scenario_labels().launch


@pytest.mark.parametrize(
    ("text", "seconds"), [("0:42", 42), ("12:05", 725), ("1:2", None), ("12:30:00", None)]
)
def test_a_duration_is_minutes_and_seconds(text: str, seconds: int | None) -> None:
    assert duration_seconds(text) == seconds


def test_a_mini_app_is_a_webview_in_the_hierarchy() -> None:
    page = parse_dump(_dump(_node("[0,200][1080,2400]", cls="android.webkit.WebView")))

    assert webview_shown(page)
    assert not webview_shown(parse_dump(_dump(_node("[0,0][10,10]"))))


def test_a_lists_rows_are_the_wide_text_rows_under_its_header() -> None:
    nodes = parse_dump(
        _dump(
            _node("[0,300][1080,380]", text="Чати"),
            _node("[0,400][1080,520]", text="Anna"),
            _node("[0,520][1080,640]", text="Daily Deals"),
            _node("[900,400][1080,520]", text="12:01"),
            _node("[0,100][1080,200]", text="Above the header"),
        )
    )
    header = find(nodes, scenario_labels().folder_chats_header)

    assert header is not None
    assert [row.text for row in rows_below(nodes, header)] == ["Anna", "Daily Deals"]


def test_search_results_that_say_channel_are_channel_rows() -> None:
    nodes = parse_dump(
        _dump(
            _node("[0,400][1080,560]", text="Канал. Новости", cls="android.view.ViewGroup"),
            _node("[0,560][1080,720]", text="Anna", cls="android.view.ViewGroup"),
        )
    )

    assert [row.text for row in channel_rows(nodes)] == ["Канал. Новости"]


@pytest.mark.parametrize(
    ("key", "word"),
    [
        ("folders_add", "adicionar"),
        ("folders_create", "створити нову папку"),
        ("folder_add_chats", "incluir chats"),
        ("save", "guardar"),
        ("launch", "open app"),
        ("close_anyway", "всё равно закрыть"),
        ("ok", "гаразд"),
    ],
)
def test_the_folder_and_mini_app_labels_carry_telegrams_strings(key: str, word: str) -> None:
    assert word in getattr(scenario_labels(), key)


@pytest.mark.asyncio
async def test_a_deep_link_opens_in_the_app_quoted_for_the_devices_shell(
    console: Console,
) -> None:
    await adb.open_uri("127.0.0.1:5601", "tg://resolve?domain=demo_bot&startapp", "org.tg")

    [call] = console.shell_calls()
    assert call == [
        *("am", "start", "-a", "android.intent.action.VIEW"),
        *("-d", "'tg://resolve?domain=demo_bot&startapp'", "org.tg"),
    ]
