"""Shared fakes for the container layer: every console call is recorded, none is run."""

from __future__ import annotations

import asyncio
import io
import tarfile
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.emulator import _sensors, _topology, pod, telegram_app
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
    monkeypatch.setattr(settings.emulator, "cpu_topology_dir", tmp_path / "cpu")
    monkeypatch.setattr(settings.emulator, "battery_dir", tmp_path / "battery")
    monkeypatch.setattr(settings.emulator, "desktop_viewer", True)
    monkeypatch.setattr(settings.emulator, "poll_seconds", 0.01)
    monkeypatch.setattr(settings.emulator, "boot_timeout_seconds", 0.1)
    monkeypatch.setattr(settings.emulator, "ui_timeout_seconds", 0.1)
    # Module-level and bound to the first loop that waits on it: a fresh one per test.
    monkeypatch.setattr(pod, "_CREATE_LOCK", asyncio.Lock())
    monkeypatch.setattr(pod, "_POD_LOCKS", {})
    # Process-wide caches: each test meets its own host and phones.
    monkeypatch.setattr(_topology, "_host_cores", None)
    monkeypatch.setattr(_sensors, "_host_abi", None)
    monkeypatch.setattr(settings.emulator, "sensors_hal_dir", None)
    monkeypatch.setattr(settings.emulator, "microg_dir", None)
    monkeypatch.setattr(telegram_app, "_PACKAGES", {})
    monkeypatch.setattr(telegram_app, "_FORGOTTEN", {})
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


class Console:
    """Records every argv and its time bound; ``answer`` maps an argv to its stdout (or raises)."""

    def __init__(self, answer: Callable[[list[str]], str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.seconds: list[float] = []  # each call's bound, by index
        self.stdins: list[bytes | None] = []  # what each call was fed, by index
        self.answer = answer or (lambda _argv: "")

    async def __call__(
        self, argv: Sequence[str], *, seconds: float, step: str, stdin: bytes | None = None
    ) -> str:
        assert seconds > 0
        assert step
        self.calls.append(list(argv))
        self.seconds.append(seconds)
        self.stdins.append(stdin)
        return self.answer(list(argv))

    def archive(self, call: list[str]) -> dict[str, tuple[tarfile.TarInfo, bytes]]:
        """The tar a ``docker cp -`` call was fed: name -> (entry, content)."""
        data = self.stdins[next(i for i, c in enumerate(self.calls) if c is call)]
        assert data is not None
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            return {
                info.name: (info, (tar.extractfile(info) or io.BytesIO()).read())
                for info in tar.getmembers()
            }

    def copies_into(self, target: str) -> list[list[str]]:
        """Every ``docker cp -a -`` call that unpacks a tar at ``target`` (``name:/dir``)."""
        return [c for c in self.calls if c[1:4] == ["cp", "-a", "-"] and c[4] == target]

    def shell_calls(self) -> list[list[str]]:
        """The device-side part (after ``shell``) of every adb shell call."""
        return [call[call.index("shell") + 1 :] for call in self.calls if "shell" in call]


@pytest.fixture
def console(monkeypatch: pytest.MonkeyPatch) -> Console:
    fake = Console()
    monkeypatch.setattr("core.emulator._process.run_command", fake)
    return fake
