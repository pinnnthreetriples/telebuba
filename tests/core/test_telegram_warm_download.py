"""Tests for media consumption (``core.telegram_client._warm_media.consume_media``).

The dispatcher reads the posts itself, picks the first file of the requested kind,
counts a view, streams a bounded prefix through ``iter_download`` and, for voice, marks
the note listened. Log rows carry the kind and the byte count — never a filename.
"""

from __future__ import annotations

import asyncio
import json
import math
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.channels import GetMessagesRequest, ReadMessageContentsRequest
from telethon.tl.functions.messages import GetMessagesViewsRequest
from telethon.tl.types import (
    Document,
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeVideo,
    DocumentEmpty,
    InputMessageID,
    MessageMediaDocument,
    MessageMediaPhoto,
)

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.repositories.logs import list_recent_logs
from core.telegram_client import _warm_media, execute
from schemas.telegram_actions import WarmConsumeMedia
from tests.core.telegram_client.helpers import patch_action_client as _patch_client

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_FILENAME = "SECRET FILENAME.mp4"
_CHANNEL = "@clips"
_CHUNK = _warm_media._CHUNK
_EVENT = "telegram_warm_consume_media"


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


class _FakeStream:
    """Yields ``limit`` chunks of ``request_size`` zero bytes; ``error`` fires on the first pull."""

    def __init__(  # noqa: PLR0913 - one knob per stream behaviour the tests steer.
        self,
        request_size: int,
        limit: int,
        *,
        error: Exception | None,
        delay: float,
        delay_from: int,
        close_error: Exception | None = None,
    ) -> None:
        self._chunk = b"\0" * request_size
        self._left = limit
        self._error = error
        self._close_error = close_error
        self._delay = delay
        self._delay_from = delay_from
        self.sent = 0
        self.closed = 0

    def __aiter__(self) -> _FakeStream:
        return self

    async def __anext__(self) -> bytes:
        if self._error is not None:
            raise self._error
        if self._left <= 0:
            raise StopAsyncIteration
        # Chunks from ``delay_from`` on stall (default: the first is instant, so a timeout
        # keeps one chunk's worth; ``0`` stalls before anything arrives).
        if self.sent >= self._delay_from and self._delay:
            await asyncio.sleep(self._delay)
        self._left -= 1
        self.sent += 1
        return self._chunk

    async def close(self) -> None:
        self.closed += 1
        if self._close_error is not None:
            raise self._close_error


class _FakeClient:
    """Answers each request by its class; ``stream_errors`` are consumed one per download."""

    def __init__(
        self,
        responses: dict[type, object] | None = None,
        *,
        stream_errors: list[Exception | None] | None = None,
        delay: float = 0.0,
        delay_from: int = 1,
        close_error: Exception | None = None,
    ) -> None:
        self._close_error = close_error
        self.captured: list[object] = []
        self.entities: list[str] = []
        self.downloads: list[dict[str, object]] = []
        self.streams: list[_FakeStream] = []
        self._responses = responses or {}
        self._stream_errors = list(stream_errors or [])
        self._delay = delay
        self._delay_from = delay_from

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

    def iter_download(self, media: object, *, request_size: int, limit: int) -> _FakeStream:
        self.downloads.append({"media": media, "request_size": request_size, "limit": limit})
        error = self._stream_errors.pop(0) if self._stream_errors else None
        stream = _FakeStream(
            request_size,
            limit,
            error=error,
            delay=self._delay,
            delay_from=self._delay_from,
            close_error=self._close_error,
        )
        self.streams.append(stream)
        return stream


async def _extra(event: str) -> dict[str, object]:
    rows = [r for r in await list_recent_logs(limit=20) if r.event == event]
    assert rows
    return rows[0].extra


def _document(*attributes: object, mime_type: str, size: int = 10 * _CHUNK) -> Document:
    return Document(
        id=5,
        access_hash=6,
        file_reference=b"ref",
        date=None,
        mime_type=mime_type,
        size=size,
        dc_id=2,
        attributes=[DocumentAttributeFilename(file_name=_FILENAME), *attributes],  # ty: ignore[invalid-argument-type]
    )


def _video(size: int = 10 * _CHUNK) -> MessageMediaDocument:
    return MessageMediaDocument(
        document=_document(
            DocumentAttributeVideo(duration=9.0, w=640, h=480), mime_type="video/mp4", size=size
        )
    )


def _round() -> MessageMediaDocument:
    return MessageMediaDocument(
        document=_document(
            DocumentAttributeVideo(duration=9.0, w=240, h=240, round_message=True),
            mime_type="video/mp4",
        )
    )


def _voice() -> MessageMediaDocument:
    return MessageMediaDocument(
        document=_document(DocumentAttributeAudio(duration=4, voice=True), mime_type="audio/ogg")
    )


def _post(message_id: int, media: object = None) -> MagicMock:
    return MagicMock(id=message_id, media=media)


def _posts(*posts: object) -> MagicMock:
    return MagicMock(messages=list(posts))


def _consume(
    kind: str = "video", max_bytes: int = 3 * _CHUNK, ids: list[int] | None = None
) -> WarmConsumeMedia:
    return WarmConsumeMedia(
        channel=_CHANNEL,
        message_ids=ids or [10, 11],
        kind=kind,  # ty: ignore[invalid-argument-type]
        max_bytes=max_bytes,
    )


def _client(media: object, **kwargs: object) -> _FakeClient:
    return _FakeClient({GetMessagesRequest: _posts(_post(10), _post(11, media))}, **kwargs)  # ty: ignore[invalid-argument-type]


# --- success --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_video_counts_a_view_then_streams_a_bounded_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(_video())
    _patch_client(monkeypatch, client)

    result = await execute("acc-m1", _consume(max_bytes=3 * _CHUNK))

    assert result.status == "ok"
    assert result.message_id == 11
    assert client.entities == [_CHANNEL]
    fetch, views = client.captured
    assert isinstance(fetch, GetMessagesRequest)
    assert fetch.id == [InputMessageID(10), InputMessageID(11)]
    assert isinstance(views, GetMessagesViewsRequest)
    assert views.peer == f"peer:{_CHANNEL}"
    assert views.id == [11]
    assert views.increment is True
    (download,) = client.downloads
    assert isinstance(download["media"], Document)
    assert download["request_size"] == _CHUNK
    assert _CHUNK % 4096 == 0
    assert download["limit"] == math.ceil(3 * _CHUNK / _CHUNK) == 3
    (stream,) = client.streams
    assert stream.sent == 3
    assert stream.closed == 1
    extra = await _extra(_EVENT)
    assert extra["kind"] == "video"
    assert extra["bytes"] == 3 * _CHUNK
    assert extra["max_bytes"] == 3 * _CHUNK
    assert extra["channel"] == _CHANNEL
    assert _FILENAME not in json.dumps(extra)


@pytest.mark.asyncio
async def test_download_limit_is_the_ceiling_of_the_smaller_of_budget_and_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A 1.5-chunk file under a 3-chunk budget: two chunk requests (the file), not three.
    size = _CHUNK + _CHUNK // 2
    client = _client(_video(size=size))
    _patch_client(monkeypatch, client)

    result = await execute("acc-m2", _consume(max_bytes=3 * _CHUNK))

    assert result.status == "ok"
    (download,) = client.downloads
    assert download["limit"] == math.ceil(size / _CHUNK) == 2


@pytest.mark.asyncio
async def test_a_sub_chunk_budget_is_one_request(monkeypatch: pytest.MonkeyPatch) -> None:
    # 65536 is half a chunk: the limit rounds up to one request, nothing else bounds the loop.
    client = _client(_video())
    _patch_client(monkeypatch, client)

    result = await execute("acc-m3", _consume(max_bytes=65_536))

    assert result.status == "ok"
    (download,) = client.downloads
    assert download["limit"] == 1
    (stream,) = client.streams
    assert stream.sent == 1
    assert stream.closed == 1
    # Chunk granularity: the last request may overshoot, never by a whole chunk.
    assert (await _extra(_EVENT))["bytes"] == _CHUNK


@pytest.mark.asyncio
async def test_voice_also_marks_the_note_listened(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(_voice())
    _patch_client(monkeypatch, client)

    result = await execute("acc-m4", _consume(kind="voice"))

    assert result.status == "ok"
    assert result.message_id == 11
    assert [type(r) for r in client.captured] == [
        GetMessagesRequest,
        GetMessagesViewsRequest,
        ReadMessageContentsRequest,
    ]
    read = client.captured[-1]
    assert isinstance(read, ReadMessageContentsRequest)
    assert read.channel == f"peer:{_CHANNEL}"
    assert read.id == [11]
    extra = await _extra(_EVENT)
    assert extra["kind"] == "voice"
    assert extra["bytes"] == 3 * _CHUNK


@pytest.mark.asyncio
async def test_video_never_marks_contents_read(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(_video())
    _patch_client(monkeypatch, client)

    await execute("acc-m5", _consume(kind="video"))

    assert not [r for r in client.captured if isinstance(r, ReadMessageContentsRequest)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "media"),
    [("video", _video()), ("voice", _voice()), ("voice", _round())],
    ids=["video", "voice_note", "round_video_as_voice"],
)
async def test_kind_selection_accepts_matching_files(
    monkeypatch: pytest.MonkeyPatch, kind: str, media: object
) -> None:
    client = _client(media)
    _patch_client(monkeypatch, client)

    result = await execute("acc-m6", _consume(kind=kind))

    assert result.status == "ok"
    assert result.message_id == 11


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "media"),
    [
        ("video", _round()),
        ("video", _voice()),
        ("voice", _video()),
        ("video", MessageMediaDocument(document=DocumentEmpty(id=1))),
        ("video", _video(size=0)),
        ("video", MessageMediaPhoto()),
        ("video", None),
    ],
    ids=[
        "round_is_not_video",
        "voice_is_not_video",
        "video_is_not_voice",
        "empty_doc",
        "no_size",
        "photo",
        "no_media",
    ],
)
async def test_skips_when_no_post_carries_a_file_of_the_kind(
    monkeypatch: pytest.MonkeyPatch, kind: str, media: object
) -> None:
    client = _client(media)
    _patch_client(monkeypatch, client)

    result = await execute("acc-m7", _consume(kind=kind))

    assert result.status == "ok"
    assert result.message_id is None
    assert [type(r) for r in client.captured] == [GetMessagesRequest]
    assert client.downloads == []
    extra = await _extra(_EVENT)
    assert extra["warm_skip"] == "no_media"
    assert "bytes" not in extra


@pytest.mark.asyncio
async def test_skips_when_the_fetch_returns_no_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient({GetMessagesRequest: MagicMock(spec=[])}))

    result = await execute("acc-m8", _consume())

    assert result.status == "ok"
    assert (await _extra(_EVENT))["warm_skip"] == "no_media"


# --- timeout ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stalled_download_ends_with_what_arrived(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_warm_media, "_DOWNLOAD_TIMEOUT_SECONDS", 0.02)
    client = _client(_video(), delay=5.0)
    _patch_client(monkeypatch, client)

    result = await execute("acc-m9", _consume(max_bytes=3 * _CHUNK))

    assert result.status == "ok"
    assert result.message_id == 11
    (stream,) = client.streams
    assert stream.sent == 1
    assert stream.closed == 1
    assert (await _extra(_EVENT))["bytes"] == _CHUNK


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["video", "voice"])
async def test_a_download_stalled_before_the_first_chunk_is_a_skip(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setattr(_warm_media, "_DOWNLOAD_TIMEOUT_SECONDS", 0.02)
    client = _client(_voice() if kind == "voice" else _video(), delay=5.0, delay_from=0)
    _patch_client(monkeypatch, client)

    result = await execute("acc-m9b", _consume(kind=kind))

    assert result.status == "ok"
    assert result.message_id is None
    # Nothing played, so a voice note is not marked listened either.
    assert not [r for r in client.captured if isinstance(r, ReadMessageContentsRequest)]
    assert client.streams[0].closed == 1
    extra = await _extra(_EVENT)
    assert extra["warm_skip"] == "download_stalled"
    assert "bytes" not in extra


@pytest.mark.asyncio
async def test_a_cancel_before_the_first_chunk_still_unwinds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``_sender`` exists before the first pull, so ``close()`` raises; the cancel must win."""
    client = _client(
        _video(),
        stream_errors=[asyncio.CancelledError()],
        close_error=AttributeError("_sender"),
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(asyncio.CancelledError):
        await execute("acc-m9d", _consume(kind="video"))
    assert client.streams[0].closed == 1


@pytest.mark.asyncio
async def test_a_timeout_raised_by_the_stream_itself_is_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only our own deadline is a "slow file"; a DC-side timeout must not read as watched."""
    client = _client(_video(), stream_errors=[TimeoutError("dc")])
    _patch_client(monkeypatch, client)

    result = await execute("acc-m9c", _consume())

    # The ladder's infrastructure rung: the request may or may not have landed.
    assert result.status == "unavailable"
    assert result.error_type == "UnconfirmedRequest"
    assert client.streams[0].closed == 1


# --- refusals ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stale",
    [
        errors.FileReferenceExpiredError(request=None),
        errors.FileReferenceInvalidError(request=None),
        errors.FilerefUpgradeNeededError(request=None),
    ],
    ids=["expired", "invalid", "upgrade_needed"],
)
async def test_stale_reference_refetches_the_post_once_and_retries(
    monkeypatch: pytest.MonkeyPatch, stale: Exception
) -> None:
    client = _client(_video(), stream_errors=[stale, None])
    _patch_client(monkeypatch, client)

    result = await execute("acc-m10", _consume())

    assert result.status == "ok"
    assert result.message_id == 11
    assert [type(r) for r in client.captured] == [
        GetMessagesRequest,
        GetMessagesViewsRequest,
        GetMessagesRequest,
    ]
    refetch = client.captured[-1]
    assert isinstance(refetch, GetMessagesRequest)
    assert refetch.id == [InputMessageID(11)]
    assert len(client.downloads) == 2
    assert [s.closed for s in client.streams] == [1, 1]
    assert (await _extra(_EVENT))["bytes"] == 3 * _CHUNK


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "errors_in_turn",
    [
        [
            errors.FileReferenceExpiredError(request=None),
            errors.FileReferenceInvalidError(request=None),
        ],
        [
            errors.FileReferenceInvalidError(request=None),
            errors.FileReferenceExpiredError(request=None),
        ],
        [
            errors.FilerefUpgradeNeededError(request=None),
            errors.FilerefUpgradeNeededError(request=None),
        ],
    ],
    ids=["expired_then_invalid", "invalid_then_expired", "upgrade_needed_twice"],
)
async def test_stale_reference_twice_is_a_skip(
    monkeypatch: pytest.MonkeyPatch, errors_in_turn: list[Exception]
) -> None:
    client = _client(_video(), stream_errors=errors_in_turn)
    _patch_client(monkeypatch, client)

    result = await execute("acc-m11", _consume())

    assert result.status == "ok"
    assert result.message_id is None
    assert len(client.downloads) == 2
    extra = await _extra(_EVENT)
    assert extra["warm_skip"] == "stale_reference"
    assert "bytes" not in extra


@pytest.mark.asyncio
async def test_stale_reference_with_the_post_gone_on_refetch_is_a_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RefetchFindsNothing(_FakeClient):
        """The second read of the post finds its file gone."""

        async def __call__(self, request: object) -> object:
            already_read = any(isinstance(r, GetMessagesRequest) for r in self.captured)
            if isinstance(request, GetMessagesRequest) and already_read:
                self.captured.append(request)
                return _posts(_post(11))
            return await super().__call__(request)

    client = _RefetchFindsNothing(
        {GetMessagesRequest: _posts(_post(11, _video()))},
        stream_errors=[errors.FileReferenceExpiredError(request=None)],
    )
    _patch_client(monkeypatch, client)

    result = await execute("acc-m12", _consume())

    assert result.status == "ok"
    assert len(client.downloads) == 1
    assert (await _extra(_EVENT))["warm_skip"] == "stale_reference"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        errors.LocationInvalidError(request=None),
        errors.FileIdInvalidError(request=None),
        errors.MediaEmptyError(request=None),
    ],
    ids=["location", "file_id", "media_empty"],
)
async def test_unreachable_media_is_a_skip(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    client = _client(_video(), stream_errors=[error])
    _patch_client(monkeypatch, client)

    result = await execute("acc-m13", _consume())

    assert result.status == "ok"
    assert result.message_id is None
    assert len(client.downloads) == 1
    assert client.streams[0].closed == 1
    assert (await _extra(_EVENT))["warm_skip"] == "media_unavailable"


@pytest.mark.asyncio
async def test_post_gone_on_the_view_count_is_a_skip_without_a_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(_video())
    client._responses[GetMessagesViewsRequest] = errors.MsgIdInvalidError(request=None)
    _patch_client(monkeypatch, client)

    result = await execute("acc-m14", _consume())

    assert result.status == "ok"
    assert result.message_id is None
    assert client.downloads == []
    assert (await _extra(_EVENT))["warm_skip"] == "post_gone"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [errors.LimitInvalidError(request=None), errors.OffsetInvalidError(request=None)],
    ids=["limit", "offset"],
)
async def test_a_refused_chunk_request_is_a_stable_code_failure(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    client = _client(_video(), stream_errors=[error])
    _patch_client(monkeypatch, client)

    result = await execute("acc-m15", _consume())

    assert result.status == "failed"
    assert result.error_type == "ProfileGatewayError"
    assert result.error_message == "bad_chunk"
    assert client.streams[0].closed == 1
    extra = await _extra(f"{_EVENT}_failed")
    assert extra["error_type"] == "bad_chunk"
    assert extra["channel"] == _CHANNEL


@pytest.mark.asyncio
async def test_flood_wait_on_the_view_count_reaches_the_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(_video())
    client._responses[GetMessagesViewsRequest] = errors.FloodWaitError(request=None, capture=44)
    _patch_client(monkeypatch, client)

    result = await execute("acc-m16", _consume())

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 44
    assert client.downloads == []


@pytest.mark.asyncio
async def test_flood_wait_inside_the_download_reaches_the_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(_video(), stream_errors=[errors.FloodWaitError(request=None, capture=9)])
    _patch_client(monkeypatch, client)

    result = await execute("acc-m17", _consume())

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 9
    assert client.streams[0].closed == 1


@pytest.mark.asyncio
async def test_generic_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(_video(), stream_errors=[RuntimeError("x")])
    _patch_client(monkeypatch, client)

    result = await execute("acc-m18", _consume())

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"
