"""Shared fixtures for account service tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from schemas.outreach_protection import OutreachProtection  # noqa: TC001
from services import warming
from services.accounts import _bulk_runner, _emulator_sign_in, bulk_messages
from services.accounts import devices as devices_module
from services.accounts import mobile_emulator as emulator_module
from services.accounts import privacy as privacy_module
from services.accounts import twofa as twofa_module
from services.accounts._import_locks import _IMPORT_LOCKS
from tests.services.accounts._mobile_support import (
    _SCREEN_ACTIONS,
    _SERVICE_FAKES,
    _SIGN_IN_FAKES,
    _Container,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

# Always open, one sender at a time, no series breaks: the order of sends is the plan's.
ALWAYS_OPEN = {
    "work_start": "00:00",
    "work_end": "24:00",
    "work_days": [0, 1, 2, 3, 4, 5, 6],
    "series_size": 0,
    "concurrency": 1,
}


@dataclass
class FakeClock:
    now: datetime
    # Every runner sleep, ``None`` for "until something wakes it".
    sleeps: list[float | None] = field(default_factory=list)


# The real sessions directory, relative to the repo root. Tests in this package
# import ``.session`` credentials, so an isolation failure writes one HERE.
_REPO_SESSIONS = Path(__file__).resolve().parents[3] / "sessions"


def _repo_session_files() -> set[str]:
    """Names of ``*.session`` files sitting in the REAL sessions dir right now."""
    if not _REPO_SESSIONS.is_dir():
        return set()
    return {path.name for path in _REPO_SESSIONS.glob("*.session")}


def _clear_mobile_state() -> None:
    """Reset the module-level mobile state between tests.

    The pod's per-account start locks are loop-bound, and a login-code pacing stamp left
    by one test refuses the next test's code request for the same account as a cooldown.
    """
    emulator_module._LOCKS.clear()
    emulator_module._ACTIVE.clear()
    devices_module._LAST_CODE_REQUEST.clear()


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
    # The fleet-wide cap is deliberately process-global in production. Pytest's
    # function-scoped asyncio loops still require a fresh semaphore per test,
    # especially when mutmut executes coverage and stats in the same process.
    monkeypatch.setattr(privacy_module, "_APPLY_SEMAPHORE", asyncio.Semaphore(4))
    # ``remove_account`` holds warming's per-account lifecycle lock across stop+delete,
    # and that table is module-level and loop-bound, so a lock built in an earlier test's
    # loop raises "bound to a different event loop" here under some orderings.
    warming._ACCOUNT_LOCKS.clear()
    # The 2FA writes serialise per account through the same module-level, loop-bound
    # shape, so it needs the same reset.
    twofa_module._TWOFA_LOCKS.clear()
    _clear_mobile_state()
    reset_logging_for_tests()
    setup_logging()
    before = _repo_session_files()
    yield
    _IMPORT_LOCKS.clear()
    warming._ACCOUNT_LOCKS.clear()
    twofa_module._TWOFA_LOCKS.clear()
    _clear_mobile_state()
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
    # sessions, and failing their test run over those would be its own bug.
    #
    # It REPORTS and does not clean up, deliberately. "New since setup" is not "written
    # by this test": this directory is the one the live instance uses, so an operator
    # importing an account while the suite runs would have appeared in the diff and had
    # their credential deleted — with no backup, blamed on a test that never wrote it.
    # Deciding whether a stray ``.session`` is garbage or a live login is a human call.
    # The assert alone still closes the failure mode that mattered: a leak turns the run
    # RED, and a red run cannot pass on the artefact the way round 3's did.
    leaked = sorted(_repo_session_files() - before)
    # Both values are copied into plain locals first. Asserting on
    # ``settings.telegram.session_dir`` directly makes pytest's assertion rewriting walk
    # the attribute chain and print the whole ``Settings`` repr on failure — which
    # includes ``telegram.api_hash`` and ``auth.secret`` from the developer's own
    # environment. A guard against leaking credentials must not leak credentials.
    final_dir = Path(settings.telegram.session_dir)
    escaped = not final_dir.is_relative_to(tmp_path)
    assert leaked == [], (
        f"{leaked} appeared in the real sessions dir ({_REPO_SESSIONS}) during this test "
        f"— session_dir isolation was probably lost (a blanket monkeypatch.undo()?); "
        f"restore only the seam you patched. Left in place on purpose: inspect before "
        f"deleting, it may be a real credential another process just wrote"
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


@pytest.fixture(autouse=True)
def typing_shows(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, OutreachProtection]]:
    """Stub the bulk run's "typing…", which is on by default.

    It would call the patched gateway and sleep for real. Tests that care read the
    recorded ``(account, peer, protection)``.
    """
    shows: list[tuple[str, str, OutreachProtection]] = []

    async def _record(account_id: str, peer: str, protection: OutreachProtection) -> None:
        shows.append((account_id, peer, protection))

    monkeypatch.setattr(bulk_messages, "_show_typing", _record)
    return shows


@pytest.fixture
def empty_bulk_jobs() -> Iterator[None]:
    """Live bulk runs are tracked in module-level dicts; start and end each test empty."""
    tables = (bulk_messages._RUNS, bulk_messages._TASKS)
    for table in tables:
        table.clear()
    yield
    for table in tables:
        table.clear()


@pytest.fixture
def bulk_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Drive the bulk runner's clock by hand: a timed sleep jumps the clock forward.

    It starts on Monday 2026-10-12 at 10:00 Moscow time, inside the default window.
    """
    clock = FakeClock(now=datetime(2026, 10, 12, 7, 0, tzinfo=UTC))

    async def fake_sleep(wakeup: asyncio.Event, seconds: float | None) -> None:
        clock.sleeps.append(seconds)
        if seconds is None:
            await wakeup.wait()
            return
        clock.now += timedelta(seconds=seconds)
        await asyncio.sleep(0)

    monkeypatch.setattr(_bulk_runner, "_now", lambda: clock.now)
    monkeypatch.setattr(_bulk_runner, "_sleep", fake_sleep)
    return clock


@pytest.fixture
def mobile_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The emulator switched on, containers named ``telebuba-<account>``."""
    monkeypatch.setattr(settings.emulator, "enabled", True)
    monkeypatch.setattr(settings.emulator, "instance_prefix", "telebuba-")


@pytest.fixture
def container(monkeypatch: pytest.MonkeyPatch) -> _Container:
    """The scripted pod on the service modules' own names."""
    fake = _Container()
    for name in _SERVICE_FAKES:
        monkeypatch.setattr(emulator_module, name, getattr(fake, name))
    for name in _SIGN_IN_FAKES:
        monkeypatch.setattr(_emulator_sign_in, name, getattr(fake, name))
    for name, label in _SCREEN_ACTIONS.items():
        monkeypatch.setattr(_emulator_sign_in, name, fake.ui(label))
    return fake
