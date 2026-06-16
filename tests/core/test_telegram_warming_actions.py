"""Tests for the warming-specific Telegram actions dispatched by ``execute``."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon.tl.functions.account import UpdateStatusRequest
from telethon.tl.functions.messages import SendReactionRequest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import execute
from schemas.telegram_actions import (
    ReactToPost,
    ReadChannel,
    SendDirectMessage,
    SetOnline,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    @asynccontextmanager
    async def fake_cm(_request: object):
        yield client

    monkeypatch.setattr("core.telegram_client._actions.telegram_client", fake_cm)


@pytest.mark.asyncio
async def test_set_online_dispatches_update_status(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-1", SetOnline(online=True))

    assert result.status == "ok"
    assert any(isinstance(req, UpdateStatusRequest) for req in captured)


@pytest.mark.asyncio
async def test_read_channel_marks_history_read(monkeypatch: pytest.MonkeyPatch) -> None:
    acks: list[int] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_messages(self, _channel: str, *, limit: int) -> list[object]:
            assert limit > 0
            return [MagicMock(id=5), MagicMock(id=9)]

        async def send_read_acknowledge(self, _channel: str, *, max_id: int) -> None:
            acks.append(max_id)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-2", ReadChannel(channel="@news", message_limit=10))

    assert result.status == "ok"
    assert acks == [9]


@pytest.mark.asyncio
async def test_read_channel_with_no_messages_skips_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    acks: list[int] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_messages(self, _channel: str, *, limit: int) -> list[object]:
            assert limit > 0
            return []

        async def send_read_acknowledge(self, _channel: str, *, max_id: int) -> None:
            acks.append(max_id)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-2b", ReadChannel(channel="@news"))

    assert result.status == "ok"
    assert acks == []


@pytest.mark.asyncio
async def test_react_to_post_sends_reaction(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_messages(self, _channel: str, *, limit: int) -> list[object]:
            assert limit > 0
            return [MagicMock(id=11), MagicMock(id=12)]

        async def get_input_entity(self, channel: str) -> str:
            return f"peer:{channel}"

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-3",
        ReactToPost(channel="@news", reactions=["👍", "🔥"], message_limit=20),
    )

    assert result.status == "ok"
    assert result.message_id in {11, 12}
    assert any(isinstance(req, SendReactionRequest) for req in captured)


@pytest.mark.asyncio
async def test_react_to_post_no_messages_returns_ok_without_reaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_messages(self, _channel: str, *, limit: int) -> list[object]:
            assert limit > 0
            return []

        async def get_input_entity(self, channel: str) -> str:  # pragma: no cover
            return channel

        async def __call__(self, request: object) -> None:  # pragma: no cover
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-3b", ReactToPost(channel="@news", reactions=["👍"]))

    assert result.status == "ok"
    assert result.message_id is None
    assert captured == []


@pytest.mark.asyncio
async def test_send_dm_returns_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "typing_simulation_enabled", False)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def send_message(self, user_id: int, text: str) -> object:
            assert user_id == 555
            assert text == "hello"
            return MagicMock(id=88)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-4", SendDirectMessage(user_id=555, text="hello"))

    assert result.status == "ok"
    assert result.message_id == 88
