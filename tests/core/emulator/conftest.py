"""Shared fakes for the container layer: every console call is recorded, none is run."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.emulator import pod
from core.logging import reset_logging_for_tests, setup_logging

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    monkeypatch.setattr(settings.emulator, "adb_path", tmp_path / "adb.exe")
    monkeypatch.setattr(settings.emulator, "docker_path", tmp_path / "docker.exe")
    monkeypatch.setattr(settings.emulator, "scrcpy_path", tmp_path / "scrcpy.exe")
    monkeypatch.setattr(settings.emulator, "desktop_viewer", True)
    monkeypatch.setattr(settings.emulator, "poll_seconds", 0.01)
    monkeypatch.setattr(settings.emulator, "boot_timeout_seconds", 0.1)
    monkeypatch.setattr(settings.emulator, "ui_timeout_seconds", 0.1)
    # Module-level and bound to the first loop that waits on it: a fresh one per test.
    monkeypatch.setattr(pod, "_CREATE_LOCK", asyncio.Lock())
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


class Console:
    """Records every argv and its time bound; ``answer`` maps an argv to its stdout (or raises)."""

    def __init__(self, answer: Callable[[list[str]], str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.seconds: list[float] = []  # each call's bound, by index
        self.answer = answer or (lambda _argv: "")

    async def __call__(self, argv: Sequence[str], *, seconds: float, step: str) -> str:
        assert seconds > 0
        assert step
        self.calls.append(list(argv))
        self.seconds.append(seconds)
        return self.answer(list(argv))

    def shell_calls(self) -> list[list[str]]:
        """The device-side part (after ``shell``) of every adb shell call."""
        return [call[call.index("shell") + 1 :] for call in self.calls if "shell" in call]


@pytest.fixture
def console(monkeypatch: pytest.MonkeyPatch) -> Console:
    fake = Console()
    monkeypatch.setattr("core.emulator._process.run_command", fake)
    return fake
