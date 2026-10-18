"""Where a scenario's target is on a Telegram screen: its label first, the layout second.

A label is one of Telegram Android's own strings in every language a container may
run (``configs/telegram_ui_labels.json``, from translations.telegram.org): ``search``
is ``Search``, ``join`` is ``ChannelJoin``/``ChannelJoinNoCaps``/``ProfileJoinChannel``,
``mute`` is ``ChannelMute``/``ChannelMuteNoCaps``, ``photo`` is ``AttachPhoto``, ``play``
is ``AccActionPlay``, ``app_error`` is ``FloodWait``/``FloodWaitTime``/
``ChannelTooMuchTitle``/``ErrorOccurred`` (up to the first placeholder), ``offline`` is
``WaitingForNetwork``/``Connecting``; the folder and Mini App keys are named on their
fields. Where a label cannot say it, the layout does: the search field is the
editable box at the top, a folder's chats are the rows under the chat list's header,
a Mini App is a ``WebView`` in the hierarchy.
"""

from __future__ import annotations

import re
from functools import cache
from typing import TYPE_CHECKING, NamedTuple

from core.emulator._errors import EmulatorError
from core.emulator.ui_dump import UiNode, fields, label_words, labels

if TYPE_CHECKING:
    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_FIELD_MISSING: EmulatorRefusalCode = "emulator_field_missing"
_WEBVIEW = "android.webkit.WebView"
# A media duration as Telegram prints it next to a play button: "0:42", "12:05".
_DURATION = re.compile(r"(?<![\d:])(\d{1,2}):([0-5]\d)(?![\d:])")
_SECONDS_PER_MINUTE = 60
# The search field sits in this top share of the screen.
_SEARCH_BAND = 0.25
# A media message is at least this share of the screen tall, between header and bottom bar.
_MEDIA_MIN_HEIGHT = 0.08
_CONTENT_TOP, _CONTENT_BOTTOM = 0.12, 0.9


class ScenarioLabels(NamedTuple):
    """The scenarios' labels, casefolded: whole labels, except where noted.

    ``folders_create`` (``CreateNewFilter``), ``folders_add`` (``Add``, a recommended
    folder's button), ``folder_add_chats`` (``FilterAddChats``), ``folder_chats_header``
    (``FilterChats``), ``save`` (``Save``, ``Done``), ``launch`` (``ProfileBotOpenApp``,
    ``BotOpen``, ``BotWebAppInstantViewOpen``; never ``BotStart``, which sends /start),
    ``close_anyway`` (``BotWebViewCloseAnyway``), ``ok`` (``OK``). ``photo`` is a whole
    word in a media message's label; an ``app_error`` dialog's text starts with one of its prefixes.
    """

    search: frozenset[str]
    join: frozenset[str]
    mute: frozenset[str]
    ok: frozenset[str]
    folders_create: frozenset[str]
    folders_add: frozenset[str]
    folder_add_chats: frozenset[str]
    folder_chats_header: frozenset[str]
    save: frozenset[str]
    launch: frozenset[str]
    close_anyway: frozenset[str]
    play: frozenset[str]
    offline: frozenset[str]
    photo: tuple[str, ...]
    app_error: tuple[str, ...]


@cache
def scenario_labels() -> ScenarioLabels:
    whole = {
        name: frozenset(label_words(name))
        for name in ScenarioLabels._fields
        if name not in {"photo", "app_error"}
    }
    return ScenarioLabels(
        **whole, photo=tuple(label_words("photo")), app_error=tuple(label_words("app_error"))
    )


def node_label(node: UiNode) -> str:
    return f"{node.text} {node.description}".strip().casefold()


def screen_size(nodes: list[UiNode]) -> tuple[int, int]:
    """The screen's width and height as the dump's outermost box gives them."""
    if not nodes:
        raise EmulatorError(_EMULATOR_FIELD_MISSING)
    return max(node.bounds[2] for node in nodes), max(node.bounds[3] for node in nodes)


def channel_rows(nodes: list[UiNode]) -> list[UiNode]:
    """Chat-list rows whose accessibility text says they are channels."""
    prefixes = tuple(f"{word}." for word in labels().channel)
    return [node for node in nodes if node_label(node).startswith(prefixes)]


def channel_key(row: UiNode) -> str:
    """The channel's title in its row ("Channel. <title>. ..."): unread counts change."""
    parts = node_label(row).split(". ")
    return parts[1] if len(parts) > 1 else parts[0]


def search_field(nodes: list[UiNode]) -> UiNode | None:
    """The editable box at the top of the screen: the search field has no label of its own."""
    if not nodes:
        return None
    _width, height = screen_size(nodes)
    return next((node for node in fields(nodes) if node.bounds[3] <= _SEARCH_BAND * height), None)


def app_error(nodes: list[UiNode]) -> bool:
    """Whether a Telegram error dialog shows: too many attempts, too many communities.

    Only a dialog's own text views count: a post saying the same is no error.
    """
    prefixes = scenario_labels().app_error
    return any(
        node.text.strip().casefold().startswith(prefixes)
        for node in nodes
        if node.class_name.endswith("TextView")
    )


def offline(nodes: list[UiNode]) -> bool:
    """Whether the title says the app waits for the network or is still connecting."""
    return any(node.says(scenario_labels().offline) for node in nodes)


@cache
def _photo_word() -> re.Pattern[str]:
    """``photo`` as a whole word: "Photo, ..." is a photo, "photography" is not."""
    words = "|".join(re.escape(word) for word in scenario_labels().photo)
    return re.compile(rf"(?<!\w)(?:{words})(?!\w)")


def photos(nodes: list[UiNode]) -> list[UiNode]:
    """Photo messages in the feed: tall enough, inside it, labelled a photo."""
    if not nodes:
        return []
    _width, height = screen_size(nodes)
    word = _photo_word()
    return [
        node
        for node in nodes
        if word.search(node_label(node))
        and node.bounds[3] - node.bounds[1] >= _MEDIA_MIN_HEIGHT * height
        and node.bounds[1] >= _CONTENT_TOP * height
        and node.bounds[3] <= _CONTENT_BOTTOM * height
    ]


def duration_seconds(text: str) -> int | None:
    match = _DURATION.search(text)
    if match is None:
        return None
    return int(match[1]) * _SECONDS_PER_MINUTE + int(match[2])


def playable(nodes: list[UiNode]) -> list[tuple[UiNode, int]]:
    """Play buttons with the duration printed on their row: voice and video notes.

    The duration is the one nearest the button: a message's sending time ("12:05")
    sits on the same row too, at the bubble's far end.
    """
    durations = [
        (node, seconds) for node in nodes if node.text and (seconds := duration_seconds(node.text))
    ]
    found: list[tuple[UiNode, int]] = []
    for button in (node for node in nodes if node.says(scenario_labels().play)):
        left, top, right, bottom = button.bounds
        row = [
            (min(abs(node.bounds[0] - right), abs(left - node.bounds[2])), seconds)
            for node, seconds in durations
            if node.bounds[1] < bottom and node.bounds[3] > top
        ]
        if row:
            found.append((button, min(row)[1]))
    return found


def webview_shown(nodes: list[UiNode]) -> bool:
    return any(node.class_name == _WEBVIEW for node in nodes)


def rows_below(nodes: list[UiNode], header: UiNode) -> list[UiNode]:
    """Text rows under ``header``, as wide as half the screen: a list's entries."""
    width, _height = screen_size(nodes)
    return [
        node
        for node in nodes
        if node is not header
        and node.text.strip()
        and not node.editable
        and node.bounds[1] >= header.bounds[3]
        and node.bounds[2] - node.bounds[0] >= width // 2
    ]
