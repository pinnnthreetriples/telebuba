"""Tests for the poll vote (``core.telegram_client._warm_media``).

The dispatcher reads the posts itself, keeps only anonymous open non-quiz polls it has
not voted in, votes for exactly one option and never logs the question or the answer.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.channels import GetMessagesRequest
from telethon.tl.functions.messages import SendVoteRequest
from telethon.tl.types import (
    InputMessageID,
    MessageMediaPhoto,
    MessageMediaPoll,
    Poll,
    PollAnswer,
    PollAnswerVoters,
    PollResults,
    TextWithEntities,
)

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.repositories.logs import list_recent_logs
from core.telegram_client import execute
from schemas.telegram_actions import WarmVoteInPoll
from tests.core.telegram_client.helpers import patch_action_client as _patch_client

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_QUESTION = "SECRET QUESTION"
_ANSWERS = ("SECRET ANSWER A", "SECRET ANSWER B", "SECRET ANSWER C")
_CHANNEL = "@polls"


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


def _text(text: str) -> TextWithEntities:
    return TextWithEntities(text=text, entities=[])


def _poll_media(*, chosen: int | None = None, **flags: bool) -> MessageMediaPoll:
    """A three-answer poll; ``flags`` set ``closed`` / ``public_voters`` / ``quiz`` and co."""
    poll = Poll(
        id=1,
        question=_text(_QUESTION),
        answers=[PollAnswer(text=_text(t), option=str(i).encode()) for i, t in enumerate(_ANSWERS)],
        hash=0,
        **flags,  # ty: ignore[invalid-argument-type] - only the four bool flags are ever passed
    )
    results = PollResults(
        results=[
            PollAnswerVoters(option=str(i).encode(), voters=1, chosen=(i == chosen))
            for i in range(len(_ANSWERS))
        ],
    )
    return MessageMediaPoll(poll=poll, results=results)


def _post(message_id: int, media: object = None) -> MagicMock:
    return MagicMock(id=message_id, media=media)


def _posts(*posts: object) -> MagicMock:
    return MagicMock(messages=list(posts))


def _vote(option_index: int = 0, ids: list[int] | None = None) -> WarmVoteInPoll:
    return WarmVoteInPoll(channel=_CHANNEL, message_ids=ids or [10, 11], option_index=option_index)


# --- success --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vote_reads_the_posts_then_votes_for_one_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient({GetMessagesRequest: _posts(_post(10), _post(11, _poll_media()))})
    _patch_client(monkeypatch, client)

    result = await execute("acc-p1", _vote(option_index=1, ids=[10, 11]))

    assert result.status == "ok"
    assert result.message_id == 11
    assert client.entities == [_CHANNEL]
    fetch, vote = client.captured
    assert isinstance(fetch, GetMessagesRequest)
    assert fetch.channel == f"peer:{_CHANNEL}"
    assert fetch.id == [InputMessageID(10), InputMessageID(11)]
    assert isinstance(vote, SendVoteRequest)
    assert vote.peer == f"peer:{_CHANNEL}"
    assert vote.msg_id == 11
    assert vote.options == [b"1"]
    extra = await _extra("telegram_warm_vote_in_poll")
    assert extra["channel"] == _CHANNEL
    assert extra["options"] == len(_ANSWERS)
    dumped = json.dumps(extra)
    assert _QUESTION not in dumped
    assert not [a for a in _ANSWERS if a in dumped]


@pytest.mark.asyncio
async def test_vote_picks_the_first_eligible_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        {
            GetMessagesRequest: _posts(
                _post(10, _poll_media(closed=True)),
                _post(11, _poll_media()),
                _post(12, _poll_media()),
            ),
        },
    )
    _patch_client(monkeypatch, client)

    result = await execute("acc-p2", _vote())

    assert result.status == "ok"
    assert result.message_id == 11
    assert [type(r) for r in client.captured] == [GetMessagesRequest, SendVoteRequest]


@pytest.mark.asyncio
@pytest.mark.parametrize(("option_index", "expected"), [(0, b"0"), (2, b"2"), (3, b"0"), (7, b"1")])
async def test_vote_option_index_wraps_modulo_the_answer_count(
    monkeypatch: pytest.MonkeyPatch, option_index: int, expected: bytes
) -> None:
    client = _FakeClient({GetMessagesRequest: _posts(_post(10, _poll_media()))})
    _patch_client(monkeypatch, client)

    result = await execute("acc-p3", _vote(option_index=option_index))

    assert result.status == "ok"
    (_, vote) = client.captured
    assert isinstance(vote, SendVoteRequest)
    assert vote.options == [expected]


@pytest.mark.asyncio
async def test_vote_sends_exactly_one_option_even_for_multiple_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient({GetMessagesRequest: _posts(_post(10, _poll_media(multiple_choice=True)))})
    _patch_client(monkeypatch, client)

    result = await execute("acc-p4", _vote(option_index=2))

    assert result.status == "ok"
    (_, vote) = client.captured
    assert isinstance(vote, SendVoteRequest)
    assert len(vote.options) == 1
    assert vote.options == [b"2"]


@pytest.mark.asyncio
async def test_vote_ignores_results_without_a_voter_list(monkeypatch: pytest.MonkeyPatch) -> None:
    # ``PollResults.results`` is optional on the wire; a poll nobody voted in has none.
    media = _poll_media()
    media.results = PollResults()
    client = _FakeClient({GetMessagesRequest: _posts(_post(10, media))})
    _patch_client(monkeypatch, client)

    result = await execute("acc-p5", _vote())

    assert result.status == "ok"
    assert result.message_id == 10


# --- filters ------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "media",
    [
        _poll_media(closed=True),
        _poll_media(public_voters=True),
        _poll_media(quiz=True),
        _poll_media(chosen=1),
        MessageMediaPhoto(),
        None,
    ],
    ids=["closed", "public", "quiz", "already_voted", "photo", "no_media"],
)
async def test_vote_skips_when_no_post_carries_an_eligible_poll(
    monkeypatch: pytest.MonkeyPatch, media: object
) -> None:
    client = _FakeClient({GetMessagesRequest: _posts(_post(10, media), _post(11))})
    _patch_client(monkeypatch, client)

    result = await execute("acc-p6", _vote())

    assert result.status == "ok"
    assert result.message_id is None
    assert [type(r) for r in client.captured] == [GetMessagesRequest]
    extra = await _extra("telegram_warm_vote_in_poll")
    assert extra["warm_skip"] == "no_poll"
    assert extra["channel"] == _CHANNEL
    assert "options" not in extra


@pytest.mark.asyncio
async def test_vote_skips_a_poll_without_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    media = _poll_media()
    media.poll.answers = []
    _patch_client(monkeypatch, _FakeClient({GetMessagesRequest: _posts(_post(10, media))}))

    result = await execute("acc-p7", _vote())

    assert result.status == "ok"
    assert (await _extra("telegram_warm_vote_in_poll"))["warm_skip"] == "no_poll"


@pytest.mark.asyncio
async def test_vote_skips_when_the_fetch_returns_no_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(monkeypatch, _FakeClient({GetMessagesRequest: MagicMock(spec=[])}))

    result = await execute("acc-p8", _vote())

    assert result.status == "ok"
    assert (await _extra("telegram_warm_vote_in_poll"))["warm_skip"] == "no_poll"


# --- refusals ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (errors.MessagePollClosedError(request=None), "poll_closed"),
        (errors.RevoteNotAllowedError(request=None), "already_voted"),
        (errors.MessageIdInvalidError(request=None), "post_gone"),
    ],
    ids=["poll_closed", "revote", "id_invalid"],
)
async def test_vote_refusals_that_say_the_poll_moved_on_are_skips(
    monkeypatch: pytest.MonkeyPatch, error: Exception, code: str
) -> None:
    client = _FakeClient(
        {GetMessagesRequest: _posts(_post(10, _poll_media())), SendVoteRequest: error},
    )
    _patch_client(monkeypatch, client)

    result = await execute("acc-p9", _vote())

    assert result.status == "ok"
    assert result.message_id is None
    assert [type(r) for r in client.captured] == [GetMessagesRequest, SendVoteRequest]
    extra = await _extra("telegram_warm_vote_in_poll")
    assert extra["warm_skip"] == code
    assert extra["channel"] == _CHANNEL
    dumped = json.dumps(extra)
    assert not [a for a in _ANSWERS if a in dumped]


@pytest.mark.asyncio
async def test_vote_private_channel_is_left_to_the_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        {
            GetMessagesRequest: _posts(_post(10, _poll_media())),
            SendVoteRequest: errors.ChannelPrivateError(request=None),
        },
    )
    _patch_client(monkeypatch, client)

    result = await execute("acc-p10", _vote())

    assert result.status == "failed"
    assert result.error_type == "ChannelPrivateError"
    extra = await _extra("telegram_warm_vote_in_poll_failed")
    assert extra["channel"] == _CHANNEL


@pytest.mark.asyncio
async def test_vote_flood_wait_reaches_the_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        {
            GetMessagesRequest: _posts(_post(10, _poll_media())),
            SendVoteRequest: errors.FloodWaitError(request=None, capture=33),
        },
    )
    _patch_client(monkeypatch, client)

    result = await execute("acc-p11", _vote())

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 33


@pytest.mark.asyncio
async def test_vote_flood_on_the_fetch_reaches_the_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    flood = errors.FloodWaitError(request=None, capture=8)
    _patch_client(monkeypatch, _FakeClient({GetMessagesRequest: flood}))

    result = await execute("acc-p12", _vote())

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 8


@pytest.mark.asyncio
async def test_vote_generic_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        {GetMessagesRequest: _posts(_post(10, _poll_media())), SendVoteRequest: RuntimeError("x")},
    )
    _patch_client(monkeypatch, client)

    result = await execute("acc-p13", _vote())

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"
