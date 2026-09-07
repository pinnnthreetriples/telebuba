"""Tests for the Saved-Messages warming writes (``core.telegram_client._warm_saved``).

A note, a reminder (scheduled note, optionally cancelled again), a draft and a forward
— every one addressed to ``InputPeerSelf`` and none of them logging the text.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.messages import (
    DeleteScheduledMessagesRequest,
    ForwardMessagesRequest,
    SaveDraftRequest,
    SendMessageRequest,
)
from telethon.tl.types import (
    InputPeerSelf,
    UpdateMessageID,
    UpdateNewMessage,
    UpdateNewScheduledMessage,
    Updates,
    UpdateShortSentMessage,
)

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.repositories.logs import list_recent_logs
from core.telegram_client import execute
from core.telegram_client._warm_saved import _sent_message_id
from schemas.telegram_actions import WarmForwardToSaved, WarmSaveDraft, WarmSelfNote
from tests.core.telegram_client.helpers import patch_action_client as _patch_client

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_NOTE = "buy milk tomorrow"
_HOURS = 48.0


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


def _updates(*updates: object) -> Updates:
    return Updates(updates=list(updates), users=[], chats=[], date=None, seq=0)  # ty: ignore[invalid-argument-type]


def _short(message_id: int) -> UpdateShortSentMessage:
    return UpdateShortSentMessage(id=message_id, pts=1, pts_count=1, date=None)


# --- _sent_message_id ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        (_short(77), 77),
        (_updates(UpdateMessageID(id=5, random_id=1)), 5),
        (_updates(UpdateNewMessage(message=MagicMock(id=6), pts=1, pts_count=1)), 6),
        (_updates(UpdateNewScheduledMessage(message=MagicMock(id=9))), 9),
        (_updates(MagicMock(), UpdateMessageID(id=8, random_id=1)), 8),
        (_updates(), None),
        (MagicMock(), None),
    ],
    ids=[
        "short_sent",
        "message_id",
        "new_message",
        "new_scheduled",
        "foreign_first",
        "empty",
        "opaque",
    ],
)
def test_sent_message_id_reads_every_updates_shape(updates: object, expected: int | None) -> None:
    assert _sent_message_id(updates) == expected


# --- self_note ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_self_note_sends_plain_text_to_self(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient({SendMessageRequest: _short(77)})
    _patch_client(monkeypatch, client)

    result = await execute("acc-n1", WarmSelfNote(text=_NOTE))

    assert result.status == "ok"
    assert result.message_id == 77
    (req,) = client.captured
    assert isinstance(req, SendMessageRequest)
    assert isinstance(req.peer, InputPeerSelf)
    assert req.message == _NOTE
    assert req.no_webpage is True
    assert req.entities is None
    assert req.schedule_date is None
    extra = await _extra("telegram_warm_self_note")
    assert extra["text_len"] == len(_NOTE)
    assert extra["scheduled"] is False
    assert extra["cancelled"] is False
    assert _NOTE not in json.dumps(extra)


@pytest.mark.asyncio
async def test_self_note_schedules_a_reminder_in_aware_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        {
            SendMessageRequest: _updates(
                UpdateMessageID(id=5, random_id=1),
                UpdateNewScheduledMessage(message=MagicMock(id=5)),
            ),
        },
    )
    _patch_client(monkeypatch, client)
    before = datetime.now(UTC)

    result = await execute("acc-n2", WarmSelfNote(text=_NOTE, schedule_in_hours=_HOURS))

    assert result.status == "ok"
    assert result.message_id == 5
    (req,) = client.captured
    assert isinstance(req, SendMessageRequest)
    assert req.schedule_date is not None
    assert req.schedule_date.tzinfo is not None
    # Rounded down to the minute, like the client's picker — so at most 60s early.
    assert (req.schedule_date.second, req.schedule_date.microsecond) == (0, 0)
    assert (
        before + timedelta(hours=_HOURS, minutes=-1)
        < req.schedule_date
        <= datetime.now(UTC) + timedelta(hours=_HOURS)
    )
    extra = await _extra("telegram_warm_self_note")
    assert extra["scheduled"] is True
    assert extra["cancelled"] is False


@pytest.mark.asyncio
async def test_self_note_cancels_the_reminder_it_just_scheduled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        {SendMessageRequest: _updates(UpdateNewScheduledMessage(message=MagicMock(id=9)))},
    )
    _patch_client(monkeypatch, client)

    result = await execute(
        "acc-n3",
        WarmSelfNote(text=_NOTE, schedule_in_hours=_HOURS, cancel_reminder=True),
    )

    assert result.status == "ok"
    assert result.message_id == 9
    send, delete = client.captured
    assert isinstance(send, SendMessageRequest)
    assert isinstance(delete, DeleteScheduledMessagesRequest)
    assert isinstance(delete.peer, InputPeerSelf)
    assert delete.id == [9]
    extra = await _extra("telegram_warm_self_note")
    assert extra["scheduled"] is True
    assert extra["cancelled"] is True
    assert _NOTE not in json.dumps(extra)


@pytest.mark.asyncio
async def test_self_note_cannot_cancel_without_a_scheduled_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient({SendMessageRequest: _updates()})
    _patch_client(monkeypatch, client)

    result = await execute(
        "acc-n4",
        WarmSelfNote(text=_NOTE, schedule_in_hours=_HOURS, cancel_reminder=True),
    )

    assert result.status == "ok"
    assert result.message_id is None
    assert len(client.captured) == 1
    extra = await _extra("telegram_warm_self_note")
    assert extra["cancelled"] is False


@pytest.mark.asyncio
async def test_self_note_cancel_flag_is_ignored_for_an_unscheduled_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient({SendMessageRequest: _short(3)})
    _patch_client(monkeypatch, client)

    result = await execute("acc-n5", WarmSelfNote(text=_NOTE, cancel_reminder=True))

    assert result.status == "ok"
    assert len(client.captured) == 1
    extra = await _extra("telegram_warm_self_note")
    assert extra["cancelled"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [errors.ScheduleDateTooLateError(request=None), errors.ScheduleDateInvalidError(request=None)],
    ids=["too_late", "invalid"],
)
async def test_self_note_refused_schedule_fails_with_a_stable_code(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    _patch_client(monkeypatch, _FakeClient({SendMessageRequest: error}))

    result = await execute("acc-n6", WarmSelfNote(text=_NOTE, schedule_in_hours=_HOURS))

    assert result.status == "failed"
    assert result.error_type == "ProfileGatewayError"
    assert result.error_message == "bad_schedule"
    extra = await _extra("telegram_warm_self_note_failed")
    assert extra["error_type"] == "bad_schedule"
    assert _NOTE not in json.dumps(extra)


@pytest.mark.asyncio
async def test_self_note_full_schedule_is_a_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    error = errors.ScheduleTooMuchError(request=None)
    _patch_client(monkeypatch, _FakeClient({SendMessageRequest: error}))

    result = await execute("acc-n7", WarmSelfNote(text=_NOTE, schedule_in_hours=_HOURS))

    assert result.status == "ok"
    assert result.message_id is None
    extra = await _extra("telegram_warm_self_note")
    assert extra["warm_skip"] == "schedule_full"
    assert extra["scheduled"] is True


@pytest.mark.asyncio
async def test_self_note_flood_wait_reaches_the_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    flood = errors.FloodWaitError(request=None, capture=21)
    _patch_client(monkeypatch, _FakeClient({SendMessageRequest: flood}))

    result = await execute("acc-n8", WarmSelfNote(text=_NOTE))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 21


@pytest.mark.asyncio
async def test_self_note_generic_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient({SendMessageRequest: RuntimeError("boom")}))

    result = await execute("acc-n9", WarmSelfNote(text=_NOTE))

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"


# --- save_draft -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_draft_writes_the_self_draft_server_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient({SaveDraftRequest: True})
    _patch_client(monkeypatch, client)

    result = await execute("acc-d1", WarmSaveDraft(text=_NOTE))

    assert result.status == "ok"
    assert result.message_id is None
    (req,) = client.captured
    assert isinstance(req, SaveDraftRequest)
    assert isinstance(req.peer, InputPeerSelf)
    assert req.message == _NOTE
    assert req.no_webpage is True
    assert req.entities is None
    extra = await _extra("telegram_warm_save_draft")
    assert extra["text_len"] == len(_NOTE)
    assert extra["clear"] is False
    assert _NOTE not in json.dumps(extra)


@pytest.mark.asyncio
async def test_save_draft_empty_text_clears_only_the_self_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient({SaveDraftRequest: True})
    _patch_client(monkeypatch, client)

    result = await execute("acc-d2", WarmSaveDraft(text=""))

    assert result.status == "ok"
    (req,) = client.captured
    assert isinstance(req, SaveDraftRequest)
    assert isinstance(req.peer, InputPeerSelf)
    assert req.message == ""
    extra = await _extra("telegram_warm_save_draft")
    assert extra["text_len"] == 0
    assert extra["clear"] is True


@pytest.mark.asyncio
async def test_save_draft_flood_wait_reaches_the_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    flood = errors.FloodWaitError(request=None, capture=4)
    _patch_client(monkeypatch, _FakeClient({SaveDraftRequest: flood}))

    result = await execute("acc-d3", WarmSaveDraft(text=_NOTE))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 4


@pytest.mark.asyncio
async def test_save_draft_generic_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient({SaveDraftRequest: RuntimeError("boom")}))

    result = await execute("acc-d4", WarmSaveDraft(text=_NOTE))

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"


# --- forward_to_saved -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_forward_to_saved_resolves_the_source_and_targets_self(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        {
            ForwardMessagesRequest: _updates(
                UpdateMessageID(id=100, random_id=1),
                UpdateNewMessage(message=MagicMock(id=100), pts=1, pts_count=1),
            ),
        },
    )
    _patch_client(monkeypatch, client)

    result = await execute("acc-f1", WarmForwardToSaved(channel="@c", message_id=42))

    assert result.status == "ok"
    assert result.message_id == 100
    assert client.entities == ["@c"]
    (req,) = client.captured
    assert isinstance(req, ForwardMessagesRequest)
    assert req.from_peer == "peer:@c"
    assert isinstance(req.to_peer, InputPeerSelf)
    assert req.id == [42]
    assert req.schedule_date is None
    extra = await _extra("telegram_warm_forward_to_saved")
    assert extra["channel"] == "@c"
    assert extra["source_id"] == 42


@pytest.mark.asyncio
async def test_forward_to_saved_short_reply_carries_the_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient({ForwardMessagesRequest: _short(101)}))

    result = await execute("acc-f2", WarmForwardToSaved(channel="@c", message_id=42))

    assert result.status == "ok"
    assert result.message_id == 101


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (errors.ChatForwardsRestrictedError(request=None), "forwards_restricted"),
        (errors.MessageIdInvalidError(request=None), "post_gone"),
        (errors.MessageIdsEmptyError(request=None), "post_gone"),
        (errors.MediaEmptyError(request=None), "media_empty"),
    ],
    ids=["forwards_restricted", "id_invalid", "ids_empty", "media_empty"],
)
async def test_forward_to_saved_lost_or_locked_posts_are_skips(
    monkeypatch: pytest.MonkeyPatch, error: Exception, code: str
) -> None:
    _patch_client(monkeypatch, _FakeClient({ForwardMessagesRequest: error}))

    result = await execute("acc-f3", WarmForwardToSaved(channel="@c", message_id=42))

    assert result.status == "ok"
    assert result.message_id is None
    extra = await _extra("telegram_warm_forward_to_saved")
    assert extra["warm_skip"] == code
    assert extra["channel"] == "@c"


@pytest.mark.asyncio
async def test_forward_to_saved_private_channel_is_left_to_the_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = errors.ChannelPrivateError(request=None)
    _patch_client(monkeypatch, _FakeClient({ForwardMessagesRequest: error}))

    result = await execute("acc-f4", WarmForwardToSaved(channel="@c", message_id=42))

    assert result.status == "failed"
    assert result.error_type == "ChannelPrivateError"


@pytest.mark.asyncio
async def test_forward_to_saved_flood_wait_reaches_the_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flood = errors.FloodWaitError(request=None, capture=60)
    _patch_client(monkeypatch, _FakeClient({ForwardMessagesRequest: flood}))

    result = await execute("acc-f5", WarmForwardToSaved(channel="@c", message_id=42))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 60


@pytest.mark.asyncio
async def test_forward_to_saved_generic_failure_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(monkeypatch, _FakeClient({ForwardMessagesRequest: RuntimeError("boom")}))

    result = await execute("acc-f6", WarmForwardToSaved(channel="@c", message_id=42))

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"
