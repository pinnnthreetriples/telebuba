"""Read Telegram's screen from a ``uiautomator dump`` — the one XML parser for it.

The container's sign-in state is whatever the screen shows NOW: nothing is cached
between calls. A container runs in its fingerprint's language, so labels are matched
in every language Telegram has a translation for (``labels``, read from
``configs/telegram_ui_labels.json``: Telegram Android's own strings for its keys,
as translations.telegram.org applies them). A password
field is recognised by the dump's own ``password="true"`` attribute, never by its label.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET  # nosec B405 - our own container's uiautomator output
from functools import cache
from typing import Literal, NamedTuple

from core.emulator._blueprints import CONFIGS_DIR

Screen = Literal["chats", "intro", "phone", "confirm", "code", "password", "unknown"]

_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
# The code screen is one box per digit; the phone screen has two fields (code + number).
_CODE_BOXES = 4
_PHONE_FIELDS = 2
_LABELS_PATH = CONFIGS_DIR / "telegram_ui_labels.json"


class Labels(NamedTuple):
    """Telegram's labels in every language, casefolded.

    Whole labels: ``start`` (the intro's button), ``yes`` (the number confirmation's
    button), ``chats`` (the chat list's menu and new-message buttons), ``phone`` (the
    phone screen's title and field), ``confirm`` (the confirmation's question). Words
    a label starts with or contains: ``channel`` (a chat row's kind), ``story`` and
    ``own_story`` (the stories row; the own one opens the camera).
    """

    start: frozenset[str]
    yes: frozenset[str]
    chats: frozenset[str]
    phone: frozenset[str]
    confirm: frozenset[str]
    channel: tuple[str, ...]
    story: tuple[str, ...]
    own_story: tuple[str, ...]


@cache
def labels() -> Labels:
    raw: dict[str, list[str]] = json.loads(_LABELS_PATH.read_text(encoding="utf-8"))

    def words(key: str) -> list[str]:
        return sorted({value.strip().casefold() for value in raw[key] if value.strip()})

    return Labels(
        start=frozenset(words("start")),
        yes=frozenset(words("yes")),
        chats=frozenset(words("chats")),
        phone=frozenset(words("phone")),
        confirm=frozenset(words("confirm")),
        channel=tuple(words("channel")),
        story=tuple(words("story")),
        own_story=tuple(words("own_story")),
    )


class UiNode(NamedTuple):
    text: str
    description: str
    class_name: str
    password: bool
    bounds: tuple[int, int, int, int]

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bounds
        return (left + right) // 2, (top + bottom) // 2

    @property
    def editable(self) -> bool:
        return self.class_name.endswith("EditText")

    def says(self, labels: frozenset[str]) -> bool:
        return self.text.strip().casefold() in labels or (
            self.description.strip().casefold() in labels
        )


def parse_dump(xml: str) -> list[UiNode]:
    """Every node with a usable on-screen box; an unreadable dump is an empty screen."""
    try:
        root = ET.fromstring(xml.strip())  # noqa: S314 # nosec B314 - see the import
    except ET.ParseError:
        return []
    nodes: list[UiNode] = []
    for element in root.iter("node"):
        match = _BOUNDS.fullmatch(element.get("bounds", ""))
        if match is None:
            continue
        left, top, right, bottom = (int(value) for value in match.groups())
        nodes.append(
            UiNode(
                text=element.get("text", ""),
                description=element.get("content-desc", ""),
                class_name=element.get("class", ""),
                password=element.get("password") == "true",
                bounds=(left, top, right, bottom),
            ),
        )
    return nodes


def find(nodes: list[UiNode], labels: frozenset[str]) -> UiNode | None:
    return next((node for node in nodes if node.says(labels)), None)


def fields(nodes: list[UiNode]) -> list[UiNode]:
    return [node for node in nodes if node.editable]


def password_field(nodes: list[UiNode]) -> UiNode | None:
    return next((node for node in fields(nodes) if node.password), None)


def classify(nodes: list[UiNode]) -> Screen:  # noqa: PLR0911 - one answer per screen
    """Which Telegram screen ``nodes`` is; the chat list only on positive evidence."""
    words = labels()
    editable = fields(nodes)
    if password_field(nodes) is not None:
        return "password"
    if not editable and find(nodes, words.chats) is not None:
        return "chats"
    if find(nodes, words.confirm) is not None and find(nodes, words.yes) is not None:
        return "confirm"
    if find(nodes, words.start) is not None:
        return "intro"
    if len(editable) >= _CODE_BOXES:
        return "code"
    if len(editable) >= _PHONE_FIELDS and find(nodes, words.phone) is not None:
        return "phone"
    return "unknown"
