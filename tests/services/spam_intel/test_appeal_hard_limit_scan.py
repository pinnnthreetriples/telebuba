"""The appeal's hard limit holds for EVERY module of the appeal, including one added later (F-012).

``test_appeal_hard_limit`` scans a fixed list of files, so a click added in a new module of the
appeal passed. Here the set is discovered: the appeal's service modules and routes by glob, every
file that imports or names ``spam_appeals`` or an ``_appeal`` module, and the two window helpers
(which may only send their CDP commands). The same forbidden-symbol scan runs over all of them,
and a planted tree proves the discovery and the scan catch a bad new file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.services.spam_intel.test_appeal_hard_limit import violations

_ROOT = Path(__file__).resolve().parents[3]

_APPEAL_GLOBS = (
    "services/spam_intel/_appeal*.py",
    "api/v1/_accounts_spam_appeal*.py",
    "core/repositories/spam_appeals.py",
    "schemas/spam_appeals.py",
)
# The window helpers drive an open window, so they alone may name their CDP error class and send
# their commands (``test_appeal_hard_limit`` pins which two commands).
_WINDOW_HELPERS = ("services/accounts/_web_target.py", "core/web_login/_navigation.py")
_WINDOW_ALLOWED = {"core.web_login._cdp", "core.web_login._cdp.CdpError", "send_command"}
_SOURCE_DIRS = ("services", "api", "core", "schemas")
# A module that imports or names the appeal's repository, schema or service modules.
_TOUCHES_APPEAL = re.compile(r"spam_appeals|spam_intel\._appeal|import _appeal|_appeal\b")


def appeal_files(root: Path) -> list[Path]:
    """Every source file under ``root`` that belongs to, or reaches, the appeal."""
    found = {path for pattern in _APPEAL_GLOBS for path in root.glob(pattern)}
    for directory in _SOURCE_DIRS:
        for path in (root / directory).rglob("*.py"):
            if _TOUCHES_APPEAL.search(path.read_text(encoding="utf-8")):
                found.add(path)
    helpers = {root / name for name in _WINDOW_HELPERS}
    return sorted(found - helpers)


def breaches(root: Path) -> dict[str, list[str]]:
    """File -> what breaks the hard limit in it, for the whole discovered set."""
    result: dict[str, list[str]] = {}
    for path in appeal_files(root):
        if found := violations(path.read_text(encoding="utf-8")):
            result[path.relative_to(root).as_posix()] = found
    for name in _WINDOW_HELPERS:
        path = root / name
        if not path.exists():
            continue
        if extra := sorted(set(violations(path.read_text(encoding="utf-8"))) - _WINDOW_ALLOWED):
            result[name] = extra
    return result


def test_the_discovery_finds_the_known_appeal_modules() -> None:
    names = {path.relative_to(_ROOT).as_posix() for path in appeal_files(_ROOT)}
    assert {
        "services/spam_intel/_appeal.py",
        "services/spam_intel/_appeal_close.py",
        "services/spam_intel/_appeal_recheck.py",
        "api/v1/_accounts_spam_appeal.py",
        "core/repositories/spam_appeals.py",
        "schemas/spam_appeals.py",
        "services/spam_intel/_card.py",
    } <= names
    assert len(names) >= 10


def test_every_module_of_the_appeal_obeys_the_hard_limit() -> None:
    assert breaches(_ROOT) == {}


def _plant(root: Path, relative: str, source: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")


@pytest.mark.parametrize(
    ("relative", "source"),
    [
        # A new module of the appeal service, found by the glob.
        (
            "services/spam_intel/_appeal_extra.py",
            "async def f(button):\n    await button.click()\n",
        ),
        # A new route module of the appeal API.
        (
            "api/v1/_accounts_spam_appeal_more.py",
            "from core.telegram_client import execute\n",
        ),
        # Any other module that reaches the appeal's repository, found by what it names.
        (
            "services/accounts/_sneaky.py",
            "from core.repositories import spam_appeals\n"
            "async def f(client):\n    await client.send_message('SpamBot', 'x')\n",
        ),
        # The window helper may send its two commands and nothing more.
        (
            "core/web_login/_navigation.py",
            "async def f(window):\n"
            "    await window.session.send_command('Input.dispatchKeyEvent')\n",
        ),
    ],
)
def test_the_scan_fails_on_a_planted_bad_file(tmp_path: Path, relative: str, source: str) -> None:
    _plant(tmp_path, "services/spam_intel/_appeal.py", "x = 1\n")  # a clean neighbour
    _plant(tmp_path, relative, source)
    assert relative in breaches(tmp_path)


def test_a_clean_planted_tree_passes(tmp_path: Path) -> None:
    _plant(tmp_path, "services/spam_intel/_appeal.py", "x = 1\n")
    _plant(
        tmp_path,
        "core/web_login/_navigation.py",
        "async def f(window):\n    await window.session.send_command('Page.navigate')\n",
    )
    assert breaches(tmp_path) == {}
