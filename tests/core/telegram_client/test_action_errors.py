"""Error classification and client configuration tests."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from PIL import Image
from telethon import errors

from core.config import settings
from core.telegram_client import UNCONFIRMED_ERROR_TYPE, create_telegram_client, execute
from core.telegram_client._pool import TelegramClientPoolError
from schemas.device_fingerprint import TelegramClientProfile
from schemas.telegram_actions import (
    CommentOnPost,
    JoinChannel,
    JoinDiscussionGroup,
    SetProfilePhoto,
)
from tests.factories import DeviceFingerprintFactory

if TYPE_CHECKING:
    from pathlib import Path


from tests.core.telegram_client.helpers import patch_action_client as _patch_client


@pytest.mark.asyncio
async def test_execute_handles_flood_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise errors.FloodWaitError(request=None, capture=42)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-5", JoinChannel(channel="@hot"))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 42
    assert result.error_type is None


@pytest.mark.asyncio
async def test_execute_handles_slow_mode_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise errors.SlowModeWaitError(request=None, capture=30)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-slow", JoinChannel(channel="@hot"))

    assert result.status == "slow_mode_wait"
    assert result.flood_wait_seconds == 30
    assert result.error_type is None


@pytest.mark.asyncio
async def test_execute_handles_premium_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise errors.FloodPremiumWaitError(request=None, capture=9)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-prem", JoinChannel(channel="@hot"))

    assert result.status == "premium_wait"
    assert result.flood_wait_seconds == 9
    assert result.error_type is None


@pytest.mark.asyncio
async def test_execute_handles_peer_flood(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise errors.PeerFloodError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-peer", JoinChannel(channel="@hot"))

    assert result.status == "peer_flood"
    assert result.flood_wait_seconds is None
    assert result.error_type is None


def test_create_telegram_client_applies_flood_sleep_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.telegram, "flood_sleep_threshold", 7)
    # Telethon refuses to build a client with an empty api_id/api_hash, so set
    # placeholders — CI has no .env, unlike a local dev machine.
    monkeypatch.setattr(settings.telegram, "api_id", 12345)
    monkeypatch.setattr(settings.telegram, "api_hash", "test-hash")
    profile = TelegramClientProfile(
        account_id="acc",
        session_path=str(tmp_path / "acc"),
        receive_updates=False,
        device=DeviceFingerprintFactory.build(
            account_id="acc",
            platform="linux",
            device_model="PC",
            system_version="Ubuntu 24.04",
            app_version="5.0.0 x64",
        ),
    )
    client = create_telegram_client(profile)
    try:
        assert client.flood_sleep_threshold == 7
    finally:
        if client.session is not None:
            client.session.close()


@pytest.mark.asyncio
async def test_execute_handles_generic_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            msg = "boom"
            raise RuntimeError(msg)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-6", JoinChannel(channel="@hot"))

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"
    assert result.error_message == "boom"


# --------------------------------------------------------------------------- #
# Actionable Telegram refusals used to collapse to the opaque ``failed``: only
# five gateway error-class names carry a stable code, and neither the dead-session
# family nor the media family was mapped to one. So a dead session and a 100x100
# avatar produced the same "Telegram refused the action — try again" toast.
# --------------------------------------------------------------------------- #
def _jpeg_bytes() -> bytes:
    """A real decodable JPEG, so the media dispatcher's Pillow gate lets it through."""
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), (200, 30, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _raising_client(exc: Exception) -> object:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise exc

        async def upload_file(self, *_args: object, **_kwargs: object) -> object:
            return object()

    return FakeClient()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (errors.AuthKeyUnregisteredError(request=None), "session_dead"),
        (errors.SessionRevokedError(request=None), "session_dead"),
        (errors.AuthKeyDuplicatedError(request=None), "session_dead"),
        (errors.UserDeactivatedBanError(request=None), "account_deactivated"),
        (errors.UserDeactivatedError(request=None), "account_deactivated"),
    ],
)
async def test_a_dead_session_gets_the_code_the_session_check_already_knows(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    code: str,
) -> None:
    """``check_telegram_session`` classifies these; the action path threw it away.

    Two codes, not one: a dead auth key is fixed by re-logging the account in,
    while a deactivated account cannot be recovered at all.
    """
    _patch_client(monkeypatch, _raising_client(exc))

    result = await execute("acc-dead", JoinChannel(channel="@hot"))

    assert result.status == "failed"
    # ProfileGatewayError is on the stable-code allowlist, so this message IS the
    # code the SPA translates (raise_for_result keeps it verbatim).
    assert result.error_type == "ProfileGatewayError"
    assert result.error_message == code


@pytest.mark.asyncio
async def test_a_deleted_dm_peer_is_not_reported_as_a_dead_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``InputUserDeactivatedError`` names the PEER, not us — it stays unmapped.

    It is a member of ``_session._ACCOUNT_ERRORS`` (where the subject IS this
    account), so sharing that tuple wholesale would tell the operator their own
    account was banned because a DM target deleted theirs.
    """
    _patch_client(monkeypatch, _raising_client(errors.InputUserDeactivatedError(request=None)))

    result = await execute("acc-peer-gone", JoinChannel(channel="@hot"))

    assert result.status == "failed"
    assert result.error_type == "InputUserDeactivatedError"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (errors.PremiumAccountRequiredError(request=None), "premium_required"),
        (errors.PhotoCropSizeSmallError(request=None), "photo_too_small"),
        (errors.PhotoInvalidDimensionsError(request=None), "photo_too_small"),
        (errors.PhotoExtInvalidError(request=None), "media_invalid"),
        (errors.PhotoInvalidError(request=None), "media_invalid"),
        (errors.ImageProcessFailedError(request=None), "media_invalid"),
        (errors.MediaEmptyError(request=None), "media_invalid"),
    ],
)
async def test_the_media_family_maps_telethon_refusals_to_stable_codes(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    code: str,
) -> None:
    """``_media.py`` had no error map at all, unlike both of its sibling dispatchers."""
    _patch_client(monkeypatch, _raising_client(exc))

    result = await execute("acc-media", SetProfilePhoto(filename="a.jpg", content=_jpeg_bytes()))

    assert result.status == "failed"
    assert result.error_type == "ProfileGatewayError"
    assert result.error_message == code


@pytest.mark.asyncio
async def test_an_unmapped_media_rpc_error_still_falls_back_to_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``failed`` stays the residual — specific codes were added ABOVE it, not instead."""
    unmapped = errors.RPCError(request=None, message="X", code=400)
    _patch_client(monkeypatch, _raising_client(unmapped))

    result = await execute("acc-media", SetProfilePhoto(filename="a.jpg", content=_jpeg_bytes()))

    assert result.status == "failed"
    assert result.error_type == "RPCError"


@pytest.mark.asyncio
async def test_a_media_flood_wait_still_reaches_the_flood_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FloodWaitError is an RPCError too, so the new map must not swallow it."""
    _patch_client(monkeypatch, _raising_client(errors.FloodWaitError(request=None, capture=11)))

    result = await execute("acc-media", SetProfilePhoto(filename="a.jpg", content=_jpeg_bytes()))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 11


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        TelegramClientPoolError("acc-7", RuntimeError("connect failed")),
        ConnectionError("socket closed"),
        TimeoutError("handshake timed out"),
    ],
)
async def test_execute_classifies_infrastructure_failures_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
) -> None:
    """Internal connection failures are unavailable, not client failures."""

    async def failing_get_client(_account_id: str) -> object:
        raise exc

    async def fake_fetch(account_id: str) -> object:
        return MagicMock(session_name=account_id)

    monkeypatch.setattr("core.telegram_client._actions.get_client", failing_get_client)
    monkeypatch.setattr("core.telegram_client._actions.fetch_account", fake_fetch)

    result = await execute("acc-7", JoinChannel(channel="@hot"))

    assert result.status == "unavailable"
    assert result.error_type == type(exc).__name__


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [ConnectionError("socket closed"), TimeoutError("read timed out")],
)
async def test_a_transient_fault_after_the_request_went_out_is_reported_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
) -> None:
    """The same exception classes mean something else once the pool handed back a client.

    ``TimeoutError`` raised while ``get_client`` connects proves nothing was sent;
    raised out of ``send_message`` it means the request is already on the wire and only
    the ANSWER was lost — Telegram may well have applied it. The class name alone cannot
    tell those apart, so the gateway (the only layer that knows which call raised)
    reports the second half under its own ``error_type``.
    """

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def send_message(self, *_args: object, **_kwargs: object) -> object:
            raise exc

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-8", CommentOnPost(channel="@chan", post_id=10, text="hi"))

    assert result.status == "unavailable"
    assert result.error_type == UNCONFIRMED_ERROR_TYPE


# --------------------------------------------------------------------------- #
# JoinDiscussionGroup — resolve the linked group from the parent, then join it
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_failed_action_logs_the_channel_it_was_acting_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure row names its channel, like the success row and the by-request row.

    Without it a failed join was the one line in the operator's feed that never said
    what it was acting on: "Вступление в чат канала — ошибка · ChannelPrivateError",
    surrounded by rows that all name theirs, and no way to tell which channel refused.
    """
    logged: list[dict[str, object]] = []

    async def fake_log(_level: str, event: str, **kwargs: object) -> None:
        logged.append({"event": event, **kwargs})

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise errors.ChannelPrivateError(request=None)

    _patch_client(monkeypatch, FakeClient())
    monkeypatch.setattr("core.telegram_client._action_results.log_event", fake_log)

    result = await execute("acc-9", JoinDiscussionGroup(channel="@MeineDNEWS"))

    assert result.status == "failed"
    assert logged == [
        {
            "event": "telegram_join_discussion_group_failed",
            "account_id": "acc-9",
            "extra": {"error_type": "ChannelPrivateError", "channel": "@MeineDNEWS"},
        },
    ]


@pytest.mark.asyncio
async def test_a_stable_code_wrapper_logs_its_code_and_still_returns_its_class_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both halves in one test, because the pair IS the contract.

    The operator reads the log row, and ``ProfileGatewayError`` is one word covering
    every refusal the wrapper carries, so a dead session read "ошибка ·
    ProfileGatewayError" while the code the SPA already labels was thrown away. Callers
    read ``error_type`` and branch on the class name, so that side must not move.
    """
    logged: list[dict[str, object]] = []

    async def fake_log(_level: str, event: str, **kwargs: object) -> None:
        logged.append({"event": event, **kwargs})

    _patch_client(monkeypatch, _raising_client(errors.AuthKeyUnregisteredError(request=None)))
    monkeypatch.setattr("core.telegram_client._action_results.log_event", fake_log)

    result = await execute("acc-dead-log", JoinChannel(channel="@hot"))

    assert logged == [
        {
            "event": "telegram_join_channel_failed",
            "account_id": "acc-dead-log",
            "extra": {"error_type": "session_dead", "channel": "@hot"},
        },
    ]
    assert result.error_type == "ProfileGatewayError"


@pytest.mark.asyncio
async def test_an_ordinary_telethon_error_still_logs_its_class_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telethon spends ``.code`` on the integer transport code, which is not the answer.

    An unmapped ``RPCError`` has a ``.code`` just like the wrappers do, but it is ``400``
    — a number that tells the operator strictly less than the class name does.
    """
    logged: list[dict[str, object]] = []

    async def fake_log(_level: str, event: str, **kwargs: object) -> None:
        logged.append({"event": event, **kwargs})

    rpc_error = errors.RPCError(request=None, message="X", code=400)
    _patch_client(monkeypatch, _raising_client(rpc_error))
    monkeypatch.setattr("core.telegram_client._action_results.log_event", fake_log)

    result = await execute("acc-rpc-log", JoinChannel(channel="@hot"))

    assert logged == [
        {
            "event": "telegram_join_channel_failed",
            "account_id": "acc-rpc-log",
            "extra": {"error_type": "RPCError", "channel": "@hot"},
        },
    ]
    assert result.error_type == "RPCError"
