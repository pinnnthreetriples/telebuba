"""The appeal's hard limit (docs/spam-intel.md 5.12, T18), pinned by construction.

The program NEVER presses an @SpamBot button, never answers one of its questions, never passes
or solves the appeal's captcha and never sends appeal text. A human does the whole appeal in the
account's own Telegram Web window. The only message the code ever sends to @SpamBot is the probe's
``/start``. Four views of the same promise:

* (a) the appeal modules and the appeal routes import nothing from ``core.telegram_client`` and
  contain no send, click or ``Input.*`` symbol (an AST scan, itself checked against a bad file);
* (b) the commands ``open_account_web`` sends for the appeal: ``tests/services/accounts/
  test_web_login_appeal.py``;
* (c) the gateway sends nothing but the probe's ``/start``, the history reader and the appeal send
  nothing at all;
* (d) the appeal routes take no body, query or path value that could become text.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from api import create_app
from api.v1 import _accounts_spam_appeal
from core.config import settings

_ROOT = Path(__file__).resolve().parents[3]

# The appeal's own code: the service modules, the routes, the repository and the contracts.
_APPEAL_FILES = (
    "services/spam_intel/_appeal.py",
    "services/spam_intel/_appeal_close.py",
    "services/spam_intel/_appeal_recheck.py",
    "api/v1/_accounts_spam_appeal.py",
    "core/repositories/spam_appeals.py",
    "schemas/spam_appeals.py",
)
# Importing any of these would put a Telegram client, a CDP input or a login driver in reach.
_FORBIDDEN_IMPORT_PREFIXES = (
    "core.telegram_client",
    "telethon",
    "core.web_login._page",
    "core.web_login._cdp",
    "core.web_login._passkey",
    "core.web_login._targets",
)
# A name, attribute or string that IS a send, a click or an input dispatch.
_FORBIDDEN_SYMBOLS = frozenset(
    {
        "send_message",
        "send_file",
        "send_read_acknowledge",
        "forward_messages",
        "edit_message",
        "delete_messages",
        "delete_dialog",
        "SendMessageRequest",
        "click",
        "press",
        "type_2fa_password",
        "_dispatch_key",
        "_click_point",
        "send_command",
        "dispatchKeyEvent",
        "dispatchMouseEvent",
        "dispatchTouchEvent",
        "insertText",
        "evaluate",
    }
)
_SEND_CALLS = frozenset(
    {
        "send_message",
        "send_file",
        "forward_messages",
        "edit_message",
        "delete_messages",
        "delete_dialog",
        "click",
    }
)


def violations(source: str) -> list[str]:
    """Every import, name, attribute or string in ``source`` that breaks the hard limit."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.startswith(_FORBIDDEN_IMPORT_PREFIXES)]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            targets = {node.module, *(f"{node.module}.{a.name}" for a in node.names)}
            found += [t for t in sorted(targets) if t.startswith(_FORBIDDEN_IMPORT_PREFIXES)]
            found += [a.name for a in node.names if a.name in _FORBIDDEN_SYMBOLS]
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_SYMBOLS:
            found.append(node.attr)
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_SYMBOLS:
            found.append(node.id)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            # Exact tokens only: docstrings may say "never click"; a CDP method name may not appear.
            and (node.value in _FORBIDDEN_SYMBOLS or node.value.startswith("Input."))
        ):
            found.append(node.value)
    return found


# --- (a): imports and symbols ---------------------------------------------------------


@pytest.mark.parametrize("relative", _APPEAL_FILES)
def test_a_the_appeal_code_imports_no_telegram_client_and_has_no_send_click_or_input(
    relative: str,
) -> None:
    source = (_ROOT / relative).read_text(encoding="utf-8")
    assert violations(source) == []


def test_a_the_window_helpers_have_no_send_click_or_input_either() -> None:
    """The service helper may name the CDP error class; ``_navigation`` sends its two commands."""
    allowed = {"core.web_login._cdp", "core.web_login._cdp.CdpError"}
    for relative in ("services/accounts/_web_target.py", "core/web_login/_navigation.py"):
        source = (_ROOT / relative).read_text(encoding="utf-8")
        found = [v for v in violations(source) if v not in allowed]
        # ``send_command`` is how ``_navigation`` sends Page.bringToFront / Page.navigate, and the
        # CDP command test above pins which two; nothing else may appear.
        assert set(found) <= {"send_command"}, relative
        if relative.startswith("services/"):
            assert found == []


def test_a_the_scan_itself_catches_each_kind_of_breach() -> None:
    bad = (
        "from core.telegram_client import check_spam_status\n"
        "from core.web_login._page import type_2fa_password\n"
        "async def f(client, window):\n"
        "    await client.send_message('SpamBot', 'text')\n"
        "    await button.click()\n"
        "    await window.session.send_command('Input.dispatchKeyEvent', {})\n"
    )
    found = set(violations(bad))
    assert {
        "core.telegram_client",
        "core.telegram_client.check_spam_status",
        "core.web_login._page.type_2fa_password",
        "type_2fa_password",
        "send_message",
        "click",
        "send_command",
        "Input.dispatchKeyEvent",
    } <= found
    assert violations("'''never click, never send_message in prose'''\nx = 1\n") == []


def test_a_the_appeal_route_opens_the_dialog_by_a_fixed_word_not_an_address() -> None:
    source = (_ROOT / "api/v1/_accounts_spam_appeal.py").read_text(encoding="utf-8")
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "open_account_web"
    ]
    assert len(calls) == 1
    (keyword,) = calls[0].keywords
    assert keyword.arg == "target"
    assert isinstance(keyword.value, ast.Constant)
    assert keyword.value.value == "spambot"


def test_a_the_only_cdp_commands_the_dialog_navigation_adds_are_a_navigation_and_a_raise() -> None:
    """The two helpers the appeal uses on an open window carry one CDP command each."""
    tree = ast.parse((_ROOT / "core/web_login/_navigation.py").read_text(encoding="utf-8"))
    commands: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in {
            "navigate_window",
            "focus_window",
        }:
            commands[node.name] = {
                c.value
                for c in ast.walk(node)
                if isinstance(c, ast.Constant)
                and isinstance(c.value, str)
                and c.value[:1].isupper()
                and "." in c.value
                and " " not in c.value
            }
    assert commands == {
        "navigate_window": {"Page.navigate"},
        "focus_window": {"Page.bringToFront"},
    }


# --- (c): what is ever sent ----------------------------------------------------------


def _spam_module() -> ast.Module:
    return ast.parse((_ROOT / "core/telegram_client/_spam.py").read_text(encoding="utf-8"))


def _call_names(node: ast.AST) -> list[tuple[str, ast.Call]]:
    return [
        (call.func.attr, call)
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
    ]


def test_c_the_only_message_the_probe_sends_is_the_start_command() -> None:
    sends = [(name, call) for name, call in _call_names(_spam_module()) if name in _SEND_CALLS]

    assert sends, "the probe must still send its /start"
    for name, call in sends:
        assert name == "send_message"
        assert len(call.args) == 2
        assert isinstance(call.args[1], ast.Name)
        assert call.args[1].id == "_START_COMMAND"
    commands = [
        node.value.value
        for node in ast.walk(_spam_module())
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_START_COMMAND" for t in node.targets)
        and isinstance(node.value, ast.Constant)
    ]
    assert commands == ["/start"]


def test_c_the_history_reader_sends_nothing() -> None:
    (reader,) = (
        node
        for node in ast.walk(_spam_module())
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "read_spambot_history"
    )
    names = {name for name, _call in _call_names(reader)}
    assert not names & (_SEND_CALLS | {"send_read_acknowledge"})
    assert "_START_COMMAND" not in {n.id for n in ast.walk(reader) if isinstance(n, ast.Name)}


def test_c_the_gateway_package_has_no_send_path_of_its_own() -> None:
    """The gateway asks through the probe seam and reads through the history reader; no more."""
    for path in sorted((_ROOT / "services/spam_intel").glob("*.py")):
        names = {name for name, _call in _call_names(ast.parse(path.read_text(encoding="utf-8")))}
        assert not names & (_SEND_CALLS | {"send_read_acknowledge", "send_command"}), path.name
    history = (_ROOT / "services/spam_intel/_history.py").read_text(encoding="utf-8")
    imported = {
        alias.name
        for node in ast.walk(ast.parse(history))
        if isinstance(node, ast.ImportFrom) and node.module == "core.telegram_client"
        for alias in node.names
    }
    assert imported == {"read_spambot_history"}


# --- (d): no free text in ------------------------------------------------------------


_APPEAL_ROUTES = {
    ("get", "/api/v1/accounts/spam-appeals"): set(),
    ("get", "/api/v1/accounts/{account_id}/spam-appeal"): {"account_id"},
    ("post", "/api/v1/accounts/{account_id}/spam-appeal/open"): {"account_id"},
    ("post", "/api/v1/accounts/{account_id}/spam-appeal/filed"): {"account_id"},
}


def test_d_the_appeal_routes_take_no_body_and_only_the_account_in_the_path() -> None:
    paths = create_app().openapi()["paths"]

    for (method, path), expected in _APPEAL_ROUTES.items():
        operation = paths[path][method]
        assert "requestBody" not in operation, path
        parameters = operation.get("parameters", [])
        # The session cookie is the operator's sign-in, not a value the handler reads.
        sent = [p for p in parameters if p["in"] != "cookie"]
        assert {p["name"] for p in sent} == expected, path
        assert {p["in"] for p in sent} <= {"path"}, path
        assert {p["name"] for p in parameters if p["in"] == "cookie"} <= {
            settings.auth.cookie_name
        }, path


def test_d_the_handlers_take_nothing_but_the_account_and_the_session_user() -> None:
    for name in (
        "list_spam_appeals",
        "get_spam_appeal",
        "open_spam_appeal_window",
        "mark_spam_appeal_filed",
    ):
        handler = getattr(_accounts_spam_appeal, name)
        assert set(inspect.signature(handler).parameters) <= {"account_id", "user"}, name
