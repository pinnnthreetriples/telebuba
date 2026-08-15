"""Fixtures every test gets, whatever the operator's ``.env`` happens to hold.

The root ``conftest.py`` stays free of pytest imports (deptry flags a dev dependency
reaching production code), so suite-wide fixtures live here instead — this file
covers everything under ``tests/`` and pytest applies it before any subpackage
conftest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_session_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point ``session_dir`` at this test's ``tmp_path``, for EVERY test.

    ``settings.telegram.session_dir`` defaults to the relative ``Path("sessions")``,
    which resolves against the CWD — for a test run, the repo root, i.e. the same
    directory name the operator's live instance keeps real credentials in. Three
    subpackage conftests (``tests/api``, ``tests/core/telegram_client``,
    ``tests/services/accounts``) already redirected it; everything else wrote there.
    They kept their own redirect: this one is the floor, not a replacement.

    Serially the escape was invisible because ``*.session`` is gitignored. Under
    ``pytest -n auto`` it is not: every worker composes the same absolute path, so
    the warming ``remove_account`` tests raced each other's unlink of one shared
    ``sessions/acc-1.session`` — the loser's ``remove_account`` died before
    ``delete_account``, leaving the row in place, and the test that asserts the
    concurrent ``start_warming`` raises ``UnknownAccountError`` failed instead.
    Isolating the directory suite-wide fixes the race and stops the leak at once.
    """
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")


@pytest.fixture(autouse=True)
def _no_ambient_deepseek_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test as a deployment that has not configured DeepSeek.

    That key is the whole switch deciding who generates text, and it is read from the
    operator's ``.env`` — which made the suite depend on ambient config. With a key
    present, every test that stubs only ``_seams.generate_text`` routed to the
    UNSTUBBED ``generate_text_deepseek`` and issued live HTTPS calls to
    api.deepseek.com: 25 of them in one run, each waiting out a 30s timeout. CI has no
    key, so CI stayed green while local runs crawled and reached the network. The
    divergence is the defect here, not the slowness.

    Blanked centrally rather than patched into each stub helper, so no later test can
    reopen the hole by stubbing one provider and forgetting the other. A test that
    means to exercise DeepSeek sets the key itself and stubs both
    (``tests/services/neurocomment/test_llm_routing.py``).
    """
    monkeypatch.setattr(settings.deepseek, "api_key", "")
