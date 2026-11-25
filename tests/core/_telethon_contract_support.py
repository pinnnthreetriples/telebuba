"""Shared helpers of the Telethon contract tests (``test_telethon_contract*``).

The fixtures that use them live in the test modules (a fixture imported from here would be
flagged as redefined by every test that asks for it).
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telethon import TelegramClient

from core.config import settings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    import pytest

ROOT = Path(__file__).resolve().parents[2]


def session_of(client: TelegramClient) -> Any:
    return client.session


def signature(callable_: Callable[..., object]) -> list[str]:
    return list(inspect.signature(callable_).parameters)


def open_clients(tmp_path: Path) -> Iterator[Callable[..., TelegramClient]]:
    """A factory of real, never-connected clients over temp ``.session`` files; closed after."""
    made: list[TelegramClient] = []

    def build(name: str = "acc", **options: Any) -> TelegramClient:
        client = TelegramClient(str(tmp_path / name), 1, "hash", **options)
        made.append(client)
        return client

    yield build
    for client in made:
        session_of(client).close()


def point_settings_at(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.telegram, "api_id", 111)
    monkeypatch.setattr(settings.telegram, "api_hash", "desktop-hash")
    monkeypatch.setattr(settings.telegram, "mobile_api_id", 6)
    monkeypatch.setattr(settings.telegram, "mobile_api_hash", "mobile-hash")
