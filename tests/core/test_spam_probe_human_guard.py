"""The human-like probe cannot press a button, answer, or delete: an AST allowlist (docs 5.7, R16).

The conversation may call exactly the Telethon methods it needs and send exactly ``/start``. A new
call on the client, or a new raw request, fails here until someone changes this list ON PURPOSE:
that is a design change (the probe is built never to press a button, answer the bot or delete the
dialog), not a patch.
"""

from __future__ import annotations

import ast
from pathlib import Path

from core.telegram_client import _spam

_SOURCE = Path(_spam.__file__).read_text(encoding="utf-8")
_TREE = ast.parse(_SOURCE)

# The functions of the conversation (the single ``/start`` probe is checked for the send only).
_CONVERSATION = (
    "converse_with_spambot",
    "converse_spam_status",
    "_collect",
    "_mark_read",
    "_pause",
    "on_reply",
)
# Everything the conversation may call on the Telethon client.
_CLIENT_METHODS = frozenset(
    {
        "get_input_entity",  # resolve @SpamBot
        "get_messages",  # open the dialog (messages.getHistory)
        "add_event_handler",
        "remove_event_handler",
        "send_message",  # only ``/start``: pinned below
        "send_read_acknowledge",  # messages.readHistory
    }
)
# Raw requests it may build, and the one action it may show.
_RAW_NAMES = frozenset({"SetTypingRequest", "SendMessageTypingAction"})
# Stems that must appear nowhere in the conversation: pressing, answering, deleting, forwarding.
_FORBIDDEN_STEMS = (
    "click",
    "press",
    "callback",
    "answer",
    "delete",
    "forward",
    "edit_message",
    "send_file",
    "send_reaction",
    "startbot",
    "inline",
    "conversation",
    "report",
    "block",
)
# Names that must not appear exactly (a stem would also catch ``get_messages``).
_FORBIDDEN_NAMES = frozenset({"get_me"})


def _functions(names: tuple[str, ...]) -> list[ast.AsyncFunctionDef]:
    found = [
        node
        for node in ast.walk(_TREE)
        if isinstance(node, ast.AsyncFunctionDef) and node.name in names
    ]
    assert {node.name for node in found} == set(names)
    return found


def _identifiers(node: ast.AST) -> set[str]:
    """Every attribute and name the code mentions (docstrings are constants, not identifiers)."""
    return {
        child.attr if isinstance(child, ast.Attribute) else child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Attribute | ast.Name)
    }


def test_the_conversation_calls_only_the_allowed_client_methods() -> None:
    used: set[str] = set()
    for function in _functions(_CONVERSATION):
        for child in ast.walk(function):
            if (
                isinstance(child, ast.Attribute)
                and isinstance(child.value, ast.Name)
                and child.value.id == "client"
            ):
                used.add(child.attr)
    assert used
    assert used <= _CLIENT_METHODS, sorted(used - _CLIENT_METHODS)


def test_the_conversation_builds_only_the_typing_request() -> None:
    raw: set[str] = set()
    for function in _functions(_CONVERSATION):
        for child in ast.walk(function):
            if not isinstance(child, ast.Attribute) or not child.attr[:1].isupper():
                continue
            root = child
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in {"functions", "types"}:
                raw.add(child.attr)
    assert raw == _RAW_NAMES


def test_no_forbidden_stem_appears_in_the_conversation() -> None:
    for function in _functions(_CONVERSATION):
        for name in _identifiers(function):
            assert not any(stem in name.lower() for stem in _FORBIDDEN_STEMS), name
            assert name not in _FORBIDDEN_NAMES, name


def test_every_send_in_the_module_is_the_start_command() -> None:
    """The legacy probe and the conversation alike: one message ever, and it is ``/start``."""
    sends = [
        node
        for node in ast.walk(_TREE)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "send_message"
    ]
    assert len(sends) == 2
    for call in sends:
        assert isinstance(call.args[1], ast.Name)
        assert call.args[1].id == "_START_COMMAND"
        assert len(call.args) == 2
        assert not call.keywords
    assert _spam._START_COMMAND == "/start"


def test_the_dialog_is_never_deleted_anywhere_in_the_module() -> None:
    mentioned = {name.lower() for name in _identifiers(_TREE)}
    assert not any("delete" in name or "click" in name or "press" in name for name in mentioned)


def test_the_guard_notices_a_button_press() -> None:
    """The scan itself is sound: a click on a message is reported by the same rules."""
    bad = ast.parse(
        "async def converse_with_spambot(client, plan):\n"
        "    message = await client.get_messages('x', limit=1)\n"
        "    await message[0].click(0)\n"
        "    await client.delete_dialog('x')\n"
    )
    names = {name.lower() for name in _identifiers(bad)}
    assert any("click" in name for name in names)
    assert any("delete" in name for name in names)
    used = {
        child.attr
        for child in ast.walk(bad)
        if isinstance(child, ast.Attribute)
        and isinstance(child.value, ast.Name)
        and child.value.id == "client"
    }
    assert not used <= _CLIENT_METHODS
