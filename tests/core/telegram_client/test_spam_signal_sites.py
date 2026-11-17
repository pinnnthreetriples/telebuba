"""Each gateway site reports its raw signal exactly once, and never changes its own answer (T11).

Sites: ``execute`` (PeerFlood by action family, FROZEN, shadow successes), ``execute_read``
(PeerFlood on a read), the chat gateway (PeerFlood and a channel ban on a send), and the session
check (``restricted`` and the freeze fields). For each the same call is made with and without a
receiver, and the result and the requests the fake client saw must be identical: a signal only
observes what a call already returned, it never asks Telegram anything.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from telethon import errors

from core.config import settings
from core.db import create_account
from core.spam_signals import drain_signals, set_signal_sink
from core.telegram_client import TelegramReadError, check_telegram_session, execute, execute_read
from core.telegram_client import _chat_writes as chat_writes
from schemas.accounts import AccountCreate
from schemas.telegram_actions import (
    GetUserProfile,
    JoinChannel,
    PostComment,
    SendChatMessage,
    UpdateProfile,
)
from schemas.telegram_session import TelegramSessionCheckRequest
from tests.core.telegram_client.helpers import patch_action_client, patch_read_client

if TYPE_CHECKING:
    from tests.core.conftest import Recorder


class _Client:
    """A client that records every request it receives and raises ``error`` on each."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[str] = []

    async def connect(self) -> None:
        return None

    async def _answer(self, name: str) -> None:
        self.calls.append(name)
        if self.error is not None:
            raise self.error

    async def __call__(self, request: object) -> None:
        await self._answer(type(request).__name__)

    async def send_message(self, *_args: object, **_kwargs: object) -> object:
        await self._answer("send_message")
        return SimpleNamespace(id=5)


async def _execute(
    monkeypatch: pytest.MonkeyPatch, action: object, error: Exception | None
) -> tuple[object, list[str]]:
    client = _Client(error)
    patch_action_client(monkeypatch, client)
    result = await execute("a1", action)  # ty: ignore[invalid-argument-type]
    await drain_signals()
    return result, client.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "kind"),
    [
        (PostComment(chat_id=900, text="hi"), "peer_flood_send"),
        (SendChatMessage(recipient="someone", text="hi"), "peer_flood_send"),
        (JoinChannel(channel="@hot"), "peer_flood_other"),
        (UpdateProfile(first_name="Alice"), "peer_flood_other"),
    ],
)
async def test_execute_reports_a_peer_flood_once_by_action_family(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder, action: object, kind: str
) -> None:
    result, _calls = await _execute(monkeypatch, action, errors.PeerFloodError(request=None))

    assert result.status == "peer_flood"  # ty: ignore[unresolved-attribute]
    assert recorder.seen == [
        (
            "a1",
            kind,
            {
                "source": "execute",
                "op": action.action_type,  # ty: ignore[unresolved-attribute]
                "detail": "PeerFlood",
            },
        )
    ]


@pytest.mark.asyncio
async def test_the_result_and_the_requests_are_the_same_with_and_without_a_receiver(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    action = PostComment(chat_id=900, text="hi")
    flood = errors.PeerFloodError(request=None)
    with_receiver = await _execute(monkeypatch, action, flood)
    set_signal_sink(None)
    without = await _execute(monkeypatch, action, flood)

    assert with_receiver == without
    assert with_receiver[1] == ["send_message"]  # one request, no second one for the signal
    assert recorder.kinds() == ["peer_flood_send"]  # and only the first run reported


@pytest.mark.asyncio
async def test_a_failing_receiver_never_changes_the_action_result(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def broken(*_args: object, **_kwargs: object) -> None:
        msg = "sink down"
        raise RuntimeError(msg)

    action = PostComment(chat_id=900, text="hi")
    flood = errors.PeerFloodError(request=None)
    expected = await _execute(monkeypatch, action, flood)
    set_signal_sink(broken)
    with caplog.at_level(logging.WARNING, logger="core.spam_signals"):
        got = await _execute(monkeypatch, action, flood)
        ok = await _execute(monkeypatch, action, None)

    assert got == expected
    assert ok[0].status == "ok"  # ty: ignore[unresolved-attribute]
    assert "RuntimeError" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        errors.FloodWaitError(request=None, capture=30),
        errors.SlowModeWaitError(request=None, capture=30),
        errors.FloodPremiumWaitError(request=None, capture=30),
    ],
)
async def test_a_flood_wait_is_not_evidence(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder, error: Exception
) -> None:
    result, _calls = await _execute(monkeypatch, JoinChannel(channel="@hot"), error)

    assert result.status in {"flood_wait", "slow_mode_wait", "premium_wait"}  # ty: ignore[unresolved-attribute]
    assert recorder.seen == []


@pytest.mark.asyncio
async def test_a_frozen_refusal_is_one_freeze_whatever_the_action_family(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    await create_account(AccountCreate(account_id="a1"))

    result, _calls = await _execute(
        monkeypatch,
        UpdateProfile(first_name="Alice"),
        errors.FrozenMethodInvalidError(request=None),
    )

    assert result.error_message == "account_frozen"  # ty: ignore[unresolved-attribute]
    assert recorder.seen == [
        (
            "a1",
            "freeze_seen",
            {"source": "execute", "op": "update_profile", "detail": "FrozenMethodInvalidError"},
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "kind"),
    [
        (PostComment(chat_id=900, text="hi"), "group_send_ok"),
        (JoinChannel(channel="@hot"), "join_ok"),
    ],
)
async def test_a_successful_group_send_or_join_is_one_shadow_success(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder, action: object, kind: str
) -> None:
    result, calls = await _execute(monkeypatch, action, None)

    assert result.status == "ok"  # ty: ignore[unresolved-attribute]
    assert len(calls) == 1
    assert recorder.kinds() == [kind]


@pytest.mark.asyncio
async def test_a_success_that_proves_nothing_about_its_target_reports_nothing(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    await _execute(monkeypatch, SendChatMessage(recipient="someone", text="hi"), None)

    assert recorder.seen == []


@pytest.mark.asyncio
async def test_a_peer_flood_on_a_read_reaches_evidence_and_keeps_its_flood_wait_kind(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    client = _Client(errors.PeerFloodError(request=None))
    patch_read_client(monkeypatch, client)

    with pytest.raises(TelegramReadError) as caught:
        await execute_read("a1", GetUserProfile())
    await drain_signals()

    assert (caught.value.kind, caught.value.reason) == ("flood_wait", "PeerFlood")
    assert caught.value.seconds is None
    assert recorder.seen == [
        ("a1", "peer_flood_other", {"source": "execute_read", "op": None, "detail": "PeerFlood"})
    ]
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_a_flood_wait_on_a_read_reports_nothing(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    patch_read_client(monkeypatch, _Client(errors.FloodWaitError(request=None, capture=9)))

    with pytest.raises(TelegramReadError) as caught:
        await execute_read("a1", GetUserProfile())
    await drain_signals()

    assert (caught.value.kind, caught.value.seconds) == ("flood_wait", 9)
    assert recorder.seen == []


class _ChatClient(_Client):
    async def get_input_entity(self, _peer: object) -> object:
        return object()

    async def get_entity(self, _peer: object) -> object:
        return SimpleNamespace(id=3)


async def _chat_send(
    monkeypatch: pytest.MonkeyPatch, error: Exception | None
) -> tuple[str | None, list[str]]:
    client = _ChatClient(error)

    async def borrow(_account_id: str) -> object:
        return client

    monkeypatch.setattr(chat_writes, "get_client", borrow)
    code = None
    try:
        await chat_writes.send_message("a1", "user", 3, text="hello")
    except chat_writes.ChatGatewayError as exc:
        code = exc.code
    await drain_signals()
    return code, client.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code", "kind"),
    [
        (errors.PeerFloodError(request=None), "chat_flood_wait", "peer_flood_send"),
        (
            errors.UserBannedInChannelError(request=None),
            "chat_write_forbidden",
            "user_banned_in_channel",
        ),
    ],
)
async def test_the_chat_gateway_reports_a_send_refusal_once_and_answers_as_before(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder, error: Exception, code: str, kind: str
) -> None:
    got_code, calls = await _chat_send(monkeypatch, error)

    assert (got_code, calls) == (code, ["send_message"])
    assert recorder.seen == [
        (
            "a1",
            kind,
            {"source": "chat_gateway", "op": "send_message", "detail": type(error).__name__},
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (errors.FloodWaitError(request=None, capture=5), "chat_flood_wait"),
        (errors.ChatWriteForbiddenError(request=None), "chat_write_forbidden"),
        (errors.UserPrivacyRestrictedError(request=None), "chat_write_forbidden"),
    ],
)
async def test_the_chat_gateway_reports_nothing_for_what_says_nothing_about_the_account(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder, error: Exception, code: str
) -> None:
    assert (await _chat_send(monkeypatch, error))[0] == code
    assert recorder.seen == []


class _SessionClient:
    """A pooled client for the session check: ``get_me`` and the app config are scripted."""

    def __init__(self, me: object, config: object | Exception) -> None:
        self.me = me
        self.config = config
        self.calls: list[str] = []

    async def is_user_authorized(self) -> bool:
        return True

    async def get_me(self) -> object:
        self.calls.append("get_me")
        return self.me

    async def __call__(self, request: object) -> object:
        self.calls.append(type(request).__name__)
        if isinstance(self.config, Exception):
            raise self.config
        return self.config

    async def download_profile_photo(self, _me: object, **_kwargs: object) -> None:
        return None


def _app_config(**fields: int) -> object:
    entries = [
        SimpleNamespace(key=key, value=SimpleNamespace(value=v)) for key, v in fields.items()
    ]
    return SimpleNamespace(config=SimpleNamespace(value=entries))


async def _check(monkeypatch: pytest.MonkeyPatch, client: _SessionClient) -> object:
    monkeypatch.setattr(settings.telegram, "api_id", 12345)
    monkeypatch.setattr(settings.telegram, "api_hash", "hash")

    async def borrow(_account_id: str, _device_type: str | None = None) -> object:
        return client

    monkeypatch.setattr("core.telegram_client._session.get_client", borrow)
    result = await check_telegram_session(TelegramSessionCheckRequest(account_id="a1"))
    await drain_signals()
    return result


@pytest.mark.asyncio
async def test_the_session_check_reports_a_restriction_and_a_freeze_it_already_read(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    me = SimpleNamespace(
        id=7,
        restricted=True,
        restriction_reason=[SimpleNamespace(platform="all", reason="terms", text="x")],
    )
    client = _SessionClient(
        me, _app_config(freeze_since_date=1_700_000_000, freeze_until_date=1_800_000_000)
    )

    result = await _check(monkeypatch, client)

    assert result.status == "frozen"  # ty: ignore[unresolved-attribute]
    assert recorder.seen == [
        ("a1", "tos_restricted", {"source": "session_check", "op": None, "detail": "all:terms"}),
        (
            "a1",
            "freeze_seen",
            {"source": "session_check", "op": None, "detail": "until=1800000000"},
        ),
    ]
    assert client.calls == ["get_me", "GetAppConfigRequest"]  # nothing added for the signals


@pytest.mark.asyncio
async def test_the_session_check_reports_the_closers_when_both_are_gone(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    client = _SessionClient(SimpleNamespace(id=7, restricted=False), _app_config())

    result = await _check(monkeypatch, client)

    assert result.status == "alive"  # ty: ignore[unresolved-attribute]
    assert recorder.kinds() == ["tos_cleared", "freeze_cleared"]


@pytest.mark.asyncio
async def test_an_app_config_that_could_not_be_read_closes_no_freeze(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    """The freeze probe is best effort: a failed read is no word that the freeze ended."""
    client = _SessionClient(SimpleNamespace(id=7), RuntimeError("no app config"))

    result = await _check(monkeypatch, client)

    assert result.status == "alive"  # ty: ignore[unresolved-attribute]
    assert recorder.seen == []


@pytest.mark.asyncio
async def test_a_frozen_refusal_raised_inside_the_session_check_is_a_freeze(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    class _Refused(_SessionClient):
        async def get_me(self) -> object:
            raise errors.FrozenMethodInvalidError(request=None)

    result = await _check(monkeypatch, _Refused(SimpleNamespace(), _app_config()))

    assert result.status == "frozen"  # ty: ignore[unresolved-attribute]
    assert recorder.seen == [
        (
            "a1",
            "freeze_seen",
            {"source": "session_check", "op": None, "detail": "FrozenMethodInvalidError"},
        )
    ]


@pytest.mark.asyncio
async def test_the_session_result_is_the_same_with_and_without_a_receiver(
    monkeypatch: pytest.MonkeyPatch, recorder: Recorder
) -> None:
    def make() -> _SessionClient:
        return _SessionClient(SimpleNamespace(id=7, restricted=True), _app_config())

    first_client, second_client = make(), make()
    first = await _check(monkeypatch, first_client)
    set_signal_sink(None)
    second = await _check(monkeypatch, second_client)

    assert first == second
    assert first_client.calls == second_client.calls
    assert recorder.kinds() == ["tos_restricted", "freeze_cleared"]
