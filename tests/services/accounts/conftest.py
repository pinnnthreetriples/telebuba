"""Shared fixtures for account service tests."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from services.accounts._import_locks import _IMPORT_LOCKS

if TYPE_CHECKING:
    from collections.abc import Iterator

# The real sessions directory, relative to the repo root. Tests in this package
# import ``.session`` credentials, so an isolation failure writes one HERE.
_REPO_SESSIONS = Path(__file__).resolve().parents[3] / "sessions"


def _repo_session_files() -> set[str]:
    """Names of ``*.session`` files sitting in the REAL sessions dir right now."""
    if not _REPO_SESSIONS.is_dir():
        return set()
    return {path.name for path in _REPO_SESSIONS.glob("*.session")}


@pytest.fixture(autouse=True)
def _isolate_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    # Per-key import locks are module-level and bind to the loop alive when first
    # awaited; clear them so each function-scoped test gets fresh locks (mirrors
    # warming's _ACCOUNT_LOCKS reset).
    _IMPORT_LOCKS.clear()
    reset_logging_for_tests()
    setup_logging()
    before = _repo_session_files()
    yield
    _IMPORT_LOCKS.clear()
    reset_logging_for_tests()
    # Isolation is not self-evident: ``monkeypatch`` is function-scoped and SHARED with
    # every test that requests it, so one ``monkeypatch.undo()`` reverts the redirect
    # above and sends an import at the real sessions dir. That happened — a test wrote a
    # credential into the working tree, and because ``*.session`` is gitignored neither
    # ``git status`` nor the suite noticed. Worse, the leftover then satisfied a later
    # run's "does this file already exist?" pre-check, so the suite went GREEN on the
    # artefact instead of on the behaviour.
    #
    # Compared by DIFF, not by emptiness: a developer's checkout legitimately holds real
    # sessions, and failing their test run over those would be its own bug. Leaked files
    # are removed as well as reported, so a single escape cannot poison every later run.
    leaked = sorted(_repo_session_files() - before)
    for name in leaked:
        (_REPO_SESSIONS / name).unlink(missing_ok=True)
    # Both values are copied into plain locals first. Asserting on
    # ``settings.telegram.session_dir`` directly makes pytest's assertion rewriting walk
    # the attribute chain and print the whole ``Settings`` repr on failure — which
    # includes ``telegram.api_hash`` and ``auth.secret`` from the developer's own
    # environment. A guard against leaking credentials must not leak credentials.
    final_dir = Path(settings.telegram.session_dir)
    escaped = not final_dir.is_relative_to(tmp_path)
    assert leaked == [], (
        f"this test wrote {leaked} into the real sessions dir — session_dir isolation "
        f"was lost (a blanket monkeypatch.undo()?); restore only the seam you patched"
    )
    assert not escaped, (
        f"session_dir points outside tmp_path at teardown ({final_dir}) — isolation was "
        f"lost mid-test, so anything written after that went into the working tree"
    )


@pytest.fixture(autouse=True)
def avatar_refresh_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub the post-mutation avatar refresh (it borrows the real client pool).

    Autouse so no media test accidentally opens a Telethon connection; tests
    that care about the refresh request the fixture and assert on the recorded
    account ids.
    """
    calls: list[str] = []

    async def _record(account_id: str) -> None:
        calls.append(account_id)

    monkeypatch.setattr("services.accounts.media.refresh_account_avatar", _record)
    return calls
