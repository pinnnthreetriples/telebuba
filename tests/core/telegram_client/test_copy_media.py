"""Media copy: what it re-sends, what it refuses, and what it retries exactly once."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.types import (
    MessageMediaDocument,
    MessageMediaEmpty,
    MessageMediaPhoto,
    MessageMediaWebPage,
)

from core.telegram_client import execute
from schemas.telegram_actions import CopyMessageMedia
from tests.core.telegram_client.helpers import patch_action_client as _patch_client

_ACTION = CopyMessageMedia(
    chat_id=555,
    source_chat="1234",
    source_message_id=7,
    caption="look at this",
    reply_to=42,
)


class _CopyClient:
    """A source message plus a scripted ``send_file`` outcome per attempt."""

    def __init__(self, media: object, outcomes: list[object]) -> None:
        self.media = media
        self.outcomes = outcomes
        self.reads = 0
        self.sends: list[dict[str, object]] = []

    async def connect(self) -> None:
        return None

    async def get_messages(self, peer: object, *, ids: int) -> object:
        self.reads += 1
        assert (peer, ids) == (1234, 7)
        return None if self.media is _MISSING else MagicMock(media=self.media)

    async def send_file(
        self,
        entity: object,
        file: object,
        *,
        caption: str | None,
        reply_to: int | None,
    ) -> object:
        self.sends.append(
            {"entity": entity, "file": file, "caption": caption, "reply_to": reply_to},
        )
        outcome = self.outcomes[len(self.sends) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


_MISSING = object()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "media",
    [MessageMediaPhoto(photo=None), MessageMediaDocument(document=None)],
)
async def test_the_media_is_copied_with_its_caption_and_reply_target(
    monkeypatch: pytest.MonkeyPatch,
    media: object,
) -> None:
    """A COPY, not a forward: the source message's media object is re-sent as ours.

    Telethon reuses the existing file reference, so nothing is uploaded again and the
    message carries no "Forwarded from" header — which is the entire point.
    """
    client = _CopyClient(media, [MagicMock(id=9001)])
    _patch_client(monkeypatch, client)

    result = await execute("acc-1", _ACTION)

    assert (result.status, result.message_id) == ("ok", 9001)
    assert client.sends == [
        {"entity": 555, "file": media, "caption": "look at this", "reply_to": 42},
    ]


@pytest.mark.asyncio
async def test_a_missing_source_message_does_not_raise_attribute_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_messages(ids=<int>)`` answers ``None`` for a message we cannot see.

    Reading ``.media`` off that would surface as an internal ``AttributeError``
    instead of a refusal the operator can act on.
    """
    client = _CopyClient(_MISSING, [])
    _patch_client(monkeypatch, client)

    result = await execute("acc-1", _ACTION)

    assert (result.status, result.error_type) == ("failed", "CopyMediaError")
    assert result.error_message == "media_source_missing"
    assert client.sends == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "media",
    [
        MessageMediaWebPage(webpage=None),  # ty: ignore[invalid-argument-type]
        MessageMediaEmpty(),
        None,
    ],
)
async def test_a_web_page_media_is_refused_before_sending(
    monkeypatch: pytest.MonkeyPatch,
    media: object,
) -> None:
    """Nothing outside the photo/document allow-list is handed to ``send_file``.

    A web-page preview makes it raise ``TypeError``, and the empty union makes it
    succeed while sending NO media — a message that silently arrives without its
    picture, which is worse than a refusal.
    """
    client = _CopyClient(media, [])
    _patch_client(monkeypatch, client)

    result = await execute("acc-1", _ACTION)

    assert result.error_message == "media_not_copyable"
    assert client.sends == []


def _numbered_reference_error() -> errors.RPCError:
    """``FILE_REFERENCE_3_EXPIRED`` — the variant Telethon does not map to a class."""
    return errors.BadRequestError(
        request=None,
        message="FILE_REFERENCE_3_EXPIRED",
        code=400,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stale",
    [
        errors.FileReferenceExpiredError(request=None),
        errors.FileReferenceInvalidError(request=None),
        errors.FileReferenceEmptyError(request=None),
        errors.FilerefUpgradeNeededError(request=None),
        _numbered_reference_error(),
    ],
)
async def test_stale_file_reference_is_refetched_once(
    monkeypatch: pytest.MonkeyPatch,
    stale: Exception,
) -> None:
    """Every shape of "that reference is dead" buys exactly one fresh read.

    ``FilerefUpgradeNeededError`` is in the list because it subclasses ``AuthKeyError``
    and would otherwise be classified as a dead session, sending the operator off to
    re-login a perfectly healthy account.
    """
    client = _CopyClient(MessageMediaPhoto(photo=None), [stale, MagicMock(id=17)])
    _patch_client(monkeypatch, client)

    result = await execute("acc-1", _ACTION)

    assert (result.status, result.message_id) == ("ok", 17)
    assert client.reads == 2


@pytest.mark.asyncio
async def test_a_reference_still_stale_after_the_refetch_is_not_a_dead_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = errors.FilerefUpgradeNeededError(request=None)
    client = _CopyClient(MessageMediaPhoto(photo=None), [stale, stale])
    _patch_client(monkeypatch, client)

    result = await execute("acc-1", _ACTION)

    assert result.error_type == "CopyMediaError"
    assert result.error_message == "media_reference_stale"
    assert client.reads == 2


@pytest.mark.asyncio
async def test_an_unrelated_rpc_failure_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the file-reference family buys a second attempt.

    Anything else reaches the executor's own ladder unchanged — retrying a send that
    Telegram refused on its merits would just spend the account's budget twice.
    """
    client = _CopyClient(
        MessageMediaPhoto(photo=None),
        [errors.ChatWriteForbiddenError(request=None)],
    )
    _patch_client(monkeypatch, client)

    result = await execute("acc-1", _ACTION)

    assert (result.status, result.error_type) == ("failed", "ChatWriteForbiddenError")
    assert client.reads == 1


@pytest.mark.asyncio
async def test_an_empty_caption_is_sent_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _CopyClient(MessageMediaPhoto(photo=None), [MagicMock(id=1)])
    _patch_client(monkeypatch, client)

    await execute("acc-1", _ACTION.model_copy(update={"caption": "", "reply_to": None}))

    assert client.sends[0]["caption"] is None
    assert client.sends[0]["reply_to"] is None
