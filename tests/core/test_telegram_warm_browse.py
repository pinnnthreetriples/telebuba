"""Tests for the ``warm_get_dialogs`` gateway read (``core.telegram_client._warm_browse``)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty
from telethon.tl.types.messages import DialogsNotModified

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.repositories.logs import list_recent_logs
from core.telegram_client import execute
from schemas.telegram_actions import WarmGetDialogs
from tests.core.telegram_client.helpers import patch_action_client as _patch_client

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


class _FakeClient:
    def __init__(self, response: object) -> None:
        self.captured: list[object] = []
        self._response = response

    async def connect(self) -> None:
        return None

    async def __call__(self, request: object) -> object:
        self.captured.append(request)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


async def _extra(event: str) -> dict[str, object]:
    rows = [r for r in await list_recent_logs(limit=20) if r.event == event]
    assert rows
    return rows[0].extra


@pytest.mark.asyncio
async def test_get_dialogs_sends_raw_request_from_the_top(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(MagicMock(dialogs=[object(), object(), object()]))
    _patch_client(monkeypatch, client)

    result = await execute("acc-d1", WarmGetDialogs(limit=25))

    assert result.status == "ok"
    (req,) = client.captured
    assert isinstance(req, GetDialogsRequest)
    assert req.hash == 0
    assert req.offset_id == 0
    assert req.offset_date is None
    assert req.limit == 25
    assert isinstance(req.offset_peer, InputPeerEmpty)
    extra = await _extra("telegram_warm_get_dialogs")
    assert extra["dialogs"] == 3
    assert extra["limit"] == 25


@pytest.mark.asyncio
async def test_get_dialogs_not_modified_counts_as_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient(DialogsNotModified(count=0)))

    result = await execute("acc-d2", WarmGetDialogs())

    assert result.status == "ok"
    assert (await _extra("telegram_warm_get_dialogs"))["dialogs"] == 0


@pytest.mark.asyncio
async def test_get_dialogs_flood_wait_reaches_the_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient(errors.FloodWaitError(request=None, capture=17)))

    result = await execute("acc-d3", WarmGetDialogs())

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 17


@pytest.mark.asyncio
async def test_get_dialogs_generic_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient(RuntimeError("boom")))

    result = await execute("acc-d4", WarmGetDialogs())

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"
