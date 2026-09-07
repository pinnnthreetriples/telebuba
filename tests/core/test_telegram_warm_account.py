"""Tests for the account-state warming reads (``core.telegram_client._warm_account``)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.account import (
    GetAccountTTLRequest,
    GetAuthorizationsRequest,
    GetAutoDownloadSettingsRequest,
    GetContentSettingsRequest,
    GetGlobalPrivacySettingsRequest,
    GetNotifySettingsRequest,
    GetPrivacyRequest,
)
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.contacts import GetContactsRequest, GetStatusesRequest
from telethon.tl.functions.help import GetAppConfigRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import (
    InputNotifyBroadcasts,
    InputNotifyChats,
    InputNotifyUsers,
    InputPrivacyKeyAbout,
    InputPrivacyKeyForwards,
    InputPrivacyKeyPhoneNumber,
    InputPrivacyKeyProfilePhoto,
    InputPrivacyKeyStatusTimestamp,
    InputUserSelf,
    UserStatusOffline,
    UserStatusOnline,
)
from telethon.tl.types.contacts import ContactsNotModified

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.repositories.logs import list_recent_logs
from core.telegram_client import execute
from schemas.telegram_actions import (
    WarmCheckSettings,
    WarmReadContacts,
    WarmReadNotifySettings,
    WarmViewProfile,
)
from tests.core.telegram_client.helpers import patch_action_client as _patch_client

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_SETTINGS_READ_TYPES = (
    GetPrivacyRequest,
    GetAuthorizationsRequest,
    GetContentSettingsRequest,
    GetGlobalPrivacySettingsRequest,
    GetAccountTTLRequest,
    GetAutoDownloadSettingsRequest,
    GetAppConfigRequest,
)


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

    async def get_input_entity(self, channel: str) -> str:
        self.entities.append(channel)
        return f"peer:{channel}"

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


# --- contacts ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_contacts_reads_list_then_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        {
            GetContactsRequest: MagicMock(contacts=[object(), object()]),
            GetStatusesRequest: [
                MagicMock(status=UserStatusOnline(expires=None)),
                MagicMock(status=UserStatusOffline(was_online=None)),
            ],
        },
    )
    _patch_client(monkeypatch, client)

    result = await execute("acc-c1", WarmReadContacts())

    assert result.status == "ok"
    contacts_req, statuses_req = client.captured
    assert isinstance(contacts_req, GetContactsRequest)
    assert contacts_req.hash == 0
    assert isinstance(statuses_req, GetStatusesRequest)
    extra = await _extra("telegram_warm_read_contacts")
    assert extra["contacts"] == 2
    assert extra["online"] == 1


@pytest.mark.asyncio
async def test_read_contacts_not_modified_counts_as_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient({GetContactsRequest: ContactsNotModified(), GetStatusesRequest: []})
    _patch_client(monkeypatch, client)

    result = await execute("acc-c2", WarmReadContacts())

    assert result.status == "ok"
    extra = await _extra("telegram_warm_read_contacts")
    assert extra["contacts"] == 0
    assert extra["online"] == 0


@pytest.mark.asyncio
async def test_read_contacts_flood_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    flood = errors.FloodWaitError(request=None, capture=33)
    _patch_client(monkeypatch, _FakeClient({GetContactsRequest: flood}))

    result = await execute("acc-c3", WarmReadContacts())

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 33


@pytest.mark.asyncio
async def test_read_contacts_generic_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient({GetStatusesRequest: RuntimeError("boom")}))

    result = await execute("acc-c4", WarmReadContacts())

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"


# --- notify settings --------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_notify_settings_reads_three_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient()
    _patch_client(monkeypatch, client)

    result = await execute("acc-n1", WarmReadNotifySettings())

    assert result.status == "ok"
    assert all(isinstance(req, GetNotifySettingsRequest) for req in client.captured)
    assert {type(req.peer) for req in client.captured} == {  # ty: ignore[unresolved-attribute]
        InputNotifyBroadcasts,
        InputNotifyUsers,
        InputNotifyChats,
    }
    assert (await _extra("telegram_warm_read_notify_settings"))["scopes"] == 3


@pytest.mark.asyncio
async def test_read_notify_settings_flood_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    flood = errors.FloodWaitError(request=None, capture=5)
    _patch_client(monkeypatch, _FakeClient({GetNotifySettingsRequest: flood}))

    result = await execute("acc-n2", WarmReadNotifySettings())

    assert result.status == "flood_wait"


@pytest.mark.asyncio
async def test_read_notify_settings_generic_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient({GetNotifySettingsRequest: RuntimeError("x")}))

    result = await execute("acc-n3", WarmReadNotifySettings())

    assert result.status == "failed"


# --- check settings ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_settings_rotates_from_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    """``offset=3`` starts at table index 3: two privacy keys, then the sessions read."""
    client = _FakeClient({GetAuthorizationsRequest: MagicMock(authorizations=[1, 2])})
    _patch_client(monkeypatch, client)

    result = await execute("acc-s1", WarmCheckSettings(calls=3, offset=3))

    assert result.status == "ok"
    first, second, third = client.captured
    assert isinstance(first, GetPrivacyRequest)
    assert isinstance(first.key, InputPrivacyKeyStatusTimestamp)
    assert isinstance(second, GetPrivacyRequest)
    assert isinstance(second.key, InputPrivacyKeyAbout)
    assert isinstance(third, GetAuthorizationsRequest)
    extra = await _extra("telegram_warm_check_settings")
    assert extra["calls"] == 3
    assert extra["sessions"] == 2


@pytest.mark.asyncio
async def test_check_settings_omits_sessions_when_that_read_did_not_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    _patch_client(monkeypatch, client)

    result = await execute("acc-s2", WarmCheckSettings(calls=1))

    assert result.status == "ok"
    (req,) = client.captured
    assert isinstance(req, GetPrivacyRequest)
    assert isinstance(req.key, InputPrivacyKeyPhoneNumber)  # default offset 0
    extra = await _extra("telegram_warm_check_settings")
    assert extra["calls"] == 1
    assert "sessions" not in extra


@pytest.mark.asyncio
async def test_check_settings_wraps_around_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """``calls=7`` (the model's cap) from ``offset=7`` reaches the table end and wraps."""
    client = _FakeClient()
    _patch_client(monkeypatch, client)

    result = await execute("acc-s3", WarmCheckSettings(calls=7, offset=7))

    assert result.status == "ok"
    assert [type(r) for r in client.captured] == [
        GetGlobalPrivacySettingsRequest,
        GetAccountTTLRequest,
        GetAutoDownloadSettingsRequest,
        GetAppConfigRequest,
        GetPrivacyRequest,
        GetPrivacyRequest,
        GetPrivacyRequest,
    ]
    assert client.captured[3].hash == 0  # ty: ignore[unresolved-attribute]
    assert [type(r.key) for r in client.captured[4:]] == [  # ty: ignore[unresolved-attribute]
        InputPrivacyKeyPhoneNumber,
        InputPrivacyKeyForwards,
        InputPrivacyKeyProfilePhoto,
    ]
    assert all(isinstance(r, _SETTINGS_READ_TYPES) for r in client.captured)


@pytest.mark.asyncio
async def test_check_settings_reaches_every_read_across_offsets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Offsets 0..10 with ``calls=1`` open each of the 11 screens exactly once."""
    client = _FakeClient()
    _patch_client(monkeypatch, client)

    for offset in range(11):
        result = await execute("acc-s4", WarmCheckSettings(calls=1, offset=offset))
        assert result.status == "ok"

    assert len(client.captured) == 11
    assert {type(r) for r in client.captured} == set(_SETTINGS_READ_TYPES)
    keys = [type(r.key) for r in client.captured if isinstance(r, GetPrivacyRequest)]
    assert len(keys) == 5
    assert set(keys) == {
        InputPrivacyKeyPhoneNumber,
        InputPrivacyKeyForwards,
        InputPrivacyKeyProfilePhoto,
        InputPrivacyKeyStatusTimestamp,
        InputPrivacyKeyAbout,
    }


@pytest.mark.asyncio
async def test_check_settings_flood_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    flood = errors.FloodWaitError(request=None, capture=9)
    _patch_client(monkeypatch, _FakeClient({GetPrivacyRequest: flood}))

    result = await execute("acc-s5", WarmCheckSettings(calls=2))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 9


@pytest.mark.asyncio
async def test_check_settings_generic_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient({GetAuthorizationsRequest: RuntimeError("x")}))

    result = await execute("acc-s6", WarmCheckSettings(calls=3, offset=3))

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"


# --- view profile -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_view_profile_self_reads_full_user(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient()
    _patch_client(monkeypatch, client)

    result = await execute("acc-p1", WarmViewProfile())

    assert result.status == "ok"
    (req,) = client.captured
    assert isinstance(req, GetFullUserRequest)
    assert isinstance(req.id, InputUserSelf)
    assert client.entities == []
    extra = await _extra("telegram_warm_view_profile")
    assert extra["kind"] == "self"
    assert "channel" not in extra


@pytest.mark.asyncio
async def test_view_profile_channel_resolves_then_reads_full_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    _patch_client(monkeypatch, client)

    result = await execute("acc-p2", WarmViewProfile(kind="channel", channel="@news"))

    assert result.status == "ok"
    assert client.entities == ["@news"]
    (req,) = client.captured
    assert isinstance(req, GetFullChannelRequest)
    assert req.channel == "peer:@news"
    extra = await _extra("telegram_warm_view_profile")
    assert extra["kind"] == "channel"
    assert extra["channel"] == "@news"


@pytest.mark.asyncio
async def test_view_profile_channel_without_handle_fails_before_any_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    _patch_client(monkeypatch, client)

    result = await execute("acc-p3", WarmViewProfile(kind="channel"))

    assert result.status == "failed"
    assert result.error_type == "ValueError"
    assert client.captured == []
    assert client.entities == []


@pytest.mark.asyncio
async def test_view_profile_flood_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    flood = errors.FloodWaitError(request=None, capture=21)
    _patch_client(monkeypatch, _FakeClient({GetFullChannelRequest: flood}))

    result = await execute("acc-p4", WarmViewProfile(kind="channel", channel="@news"))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 21


@pytest.mark.asyncio
async def test_view_profile_generic_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient({GetFullUserRequest: RuntimeError("x")}))

    result = await execute("acc-p5", WarmViewProfile())

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"
