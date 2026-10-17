"""Read Telegram's screen from a ``uiautomator dump`` — the one XML parser for it.

The container's sign-in state is whatever the screen shows NOW: nothing is cached
between calls. Labels are matched in English and Russian, the two languages the
operator's containers run in, and a password field is recognised by the dump's own
``password="true"`` attribute, never by its label.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # nosec B405 - our own container's uiautomator output
from typing import Literal, NamedTuple

Screen = Literal["chats", "intro", "phone", "confirm", "code", "password", "unknown"]

_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
# The code screen is one box per digit; the phone screen has two fields (code + number).
_CODE_BOXES = 4
_PHONE_FIELDS = 2

START_LABELS = frozenset({"start messaging", "начать общение"})
YES_LABELS = frozenset({"yes", "да"})
_CHATS_MARKERS = frozenset(
    {"open navigation menu", "открыть меню навигации", "new message", "новое сообщение"}
)
_PHONE_MARKERS = frozenset(
    {"your phone number", "ваш номер телефона", "phone number", "номер телефона"}
)
_CONFIRM_MARKERS = frozenset({"is this the correct number?", "это правильный номер?"})


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
    editable = fields(nodes)
    if password_field(nodes) is not None:
        return "password"
    if not editable and find(nodes, _CHATS_MARKERS) is not None:
        return "chats"
    if find(nodes, _CONFIRM_MARKERS) is not None and find(nodes, YES_LABELS) is not None:
        return "confirm"
    if find(nodes, START_LABELS) is not None:
        return "intro"
    if len(editable) >= _CODE_BOXES:
        return "code"
    if len(editable) >= _PHONE_FIELDS and find(nodes, _PHONE_MARKERS) is not None:
        return "phone"
    return "unknown"
