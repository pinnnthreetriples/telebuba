"""Tests for the dialog-level warming writes (``core.telegram_client._warm_chats``).

Archive / unarchive a channel and mute / unmute it — the raw folder and notify-settings
requests, the exact wire values for "back to main list" and "unmuted", and the two
stable-code refusals.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.account import UpdateNotifySettingsRequest
from telethon.tl.functions.folders import EditPeerFoldersRequest
from telethon.tl.types import InputFolderPeer, InputNotifyPeer, InputPeerNotifySettings

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.repositories.logs import list_recent_logs
from core.telegram_client import execute
from schemas.telegram_actions import WarmMutePeer, WarmToggleArchive
from tests.core.telegram_client.helpers import patch_action_client as _patch_client

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_CHANNEL = "@noisy"
_HOURS = 8.0


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
    """Answers each request by its class; a missing entry returns a bare mock."""

    def __init__(self, responses: dict[type, object] | None = None) -> None:
        self.captured: list[object] = []
        self.entities: list[str] = []
        self._responses = responses or {}

    async def connect(self) -> None:
        return None

    async def get_input_entity(self, username: str) -> str:
        self.entities.append(username)
        return f"peer:{username}"

    async def __call__(self, request: object) -> object:
        self.captured.append(request)
        response = self._responses.get(type(request), MagicMock())
        if isinstance(response, Exception):
            raise response
        return response


async def _extra(event: str) -> dict[str, object]:
    rows = [r for r in await list_recent_logs(limit=20) if r.event == event]
    assert rows
    return rows[0].extra


# --- toggle_archive -------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("archived", "folder_id"), [(True, 1), (False, 0)], ids=["archive", "unarchive"]
)
async def test_toggle_archive_moves_the_peer_between_the_two_folders(
    monkeypatch: pytest.MonkeyPatch,
    archived: bool,  # noqa: FBT001 - parametrized flag
    folder_id: int,
) -> None:
    client = _FakeClient({EditPeerFoldersRequest: MagicMock()})
    _patch_client(monkeypatch, client)

    result = await execute("acc-a1", WarmToggleArchive(channel=_CHANNEL, archived=archived))

    assert result.status == "ok"
    assert result.message_id is None
    assert client.entities == [_CHANNEL]
    (req,) = client.captured
    assert isinstance(req, EditPeerFoldersRequest)
    (folder_peer,) = req.folder_peers
    assert isinstance(folder_peer, InputFolderPeer)
    assert folder_peer.peer == f"peer:{_CHANNEL}"
    assert folder_peer.folder_id == folder_id
    extra = await _extra("telegram_warm_toggle_archive")
    assert extra == {"channel": _CHANNEL, "archived": archived}


@pytest.mark.asyncio
async def test_toggle_archive_bad_folder_fails_with_a_stable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = errors.FolderIdInvalidError(request=None)
    _patch_client(monkeypatch, _FakeClient({EditPeerFoldersRequest: error}))

    result = await execute("acc-a2", WarmToggleArchive(channel=_CHANNEL, archived=True))

    assert result.status == "failed"
    assert result.error_type == "ProfileGatewayError"
    assert result.error_message == "bad_folder"
    extra = await _extra("telegram_warm_toggle_archive_failed")
    assert extra["error_type"] == "bad_folder"
    assert extra["channel"] == _CHANNEL


@pytest.mark.asyncio
async def test_toggle_archive_flood_wait_reaches_the_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flood = errors.FloodWaitError(request=None, capture=12)
    _patch_client(monkeypatch, _FakeClient({EditPeerFoldersRequest: flood}))

    result = await execute("acc-a3", WarmToggleArchive(channel=_CHANNEL, archived=True))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 12


@pytest.mark.asyncio
async def test_toggle_archive_generic_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient({EditPeerFoldersRequest: RuntimeError("boom")}))

    result = await execute("acc-a4", WarmToggleArchive(channel=_CHANNEL, archived=False))

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"


# --- mute_peer ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mute_peer_mutes_until_an_aware_minute_floored_instant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient({UpdateNotifySettingsRequest: True})
    _patch_client(monkeypatch, client)
    before = datetime.now(UTC)

    result = await execute("acc-m1", WarmMutePeer(channel=_CHANNEL, mute_hours=_HOURS))

    assert result.status == "ok"
    assert client.entities == [_CHANNEL]
    (req,) = client.captured
    assert isinstance(req, UpdateNotifySettingsRequest)
    assert isinstance(req.peer, InputNotifyPeer)
    assert req.peer.peer == f"peer:{_CHANNEL}"
    assert isinstance(req.settings, InputPeerNotifySettings)
    until = req.settings.mute_until
    assert isinstance(until, datetime)
    assert until.tzinfo is not None
    # Rounded down to the minute, like the client's picker — so at most 60s early.
    assert (until.second, until.microsecond) == (0, 0)
    assert (
        before + timedelta(hours=_HOURS, minutes=-1)
        < until
        <= datetime.now(UTC) + timedelta(hours=_HOURS)
    )
    # Only the mute changes; every other notify field stays "as is".
    assert req.settings.show_previews is None
    assert req.settings.silent is None
    assert req.settings.sound is None
    extra = await _extra("telegram_warm_mute_peer")
    assert extra == {"channel": _CHANNEL, "mute_hours": _HOURS}


@pytest.mark.asyncio
async def test_mute_peer_zero_hours_sends_the_wire_value_for_unmuted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient({UpdateNotifySettingsRequest: True})
    _patch_client(monkeypatch, client)

    result = await execute("acc-m2", WarmMutePeer(channel=_CHANNEL, mute_hours=0))

    assert result.status == "ok"
    (req,) = client.captured
    assert isinstance(req, UpdateNotifySettingsRequest)
    assert isinstance(req.settings, InputPeerNotifySettings)
    # ``0`` (not ``None``): Telethon sets the flag bit and writes a zero timestamp, which
    # Telegram reads as "unmuted"; ``None`` would leave the mute untouched.
    assert req.settings.mute_until == 0
    assert req.settings.mute_until is not False
    extra = await _extra("telegram_warm_mute_peer")
    assert extra["mute_hours"] == 0


@pytest.mark.asyncio
async def test_mute_peer_bad_settings_fails_with_a_stable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = errors.SettingsInvalidError(request=None)
    _patch_client(monkeypatch, _FakeClient({UpdateNotifySettingsRequest: error}))

    result = await execute("acc-m3", WarmMutePeer(channel=_CHANNEL, mute_hours=_HOURS))

    assert result.status == "failed"
    assert result.error_type == "ProfileGatewayError"
    assert result.error_message == "bad_settings"
    extra = await _extra("telegram_warm_mute_peer_failed")
    assert extra["error_type"] == "bad_settings"
    assert extra["channel"] == _CHANNEL


@pytest.mark.asyncio
async def test_mute_peer_flood_wait_reaches_the_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    flood = errors.FloodWaitError(request=None, capture=45)
    _patch_client(monkeypatch, _FakeClient({UpdateNotifySettingsRequest: flood}))

    result = await execute("acc-m4", WarmMutePeer(channel=_CHANNEL, mute_hours=_HOURS))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 45


@pytest.mark.asyncio
async def test_mute_peer_generic_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient({UpdateNotifySettingsRequest: RuntimeError("boom")}))

    result = await execute("acc-m5", WarmMutePeer(channel=_CHANNEL, mute_hours=_HOURS))

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"
