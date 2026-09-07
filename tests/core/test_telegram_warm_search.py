"""Tests for the two warming reads that start from a channel's posts.

``warm_search_messages`` and ``warm_link_preview`` (``core.telegram_client._warm_browse``):
the dispatcher's own ``channels.getMessages`` first, then the word / URL pickers and the
request each one feeds. Log rows must carry lengths and counts, never the word or URL.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.channels import GetMessagesRequest
from telethon.tl.functions.messages import GetWebPageRequest, SearchGlobalRequest, SearchRequest
from telethon.tl.types import (
    InputMessageID,
    InputMessagesFilterEmpty,
    InputPeerEmpty,
    MessageEmpty,
    MessageEntityBold,
    MessageEntityTextUrl,
    MessageEntityUrl,
    MessageMediaWebPage,
    PeerChannel,
    WebPage,
    WebPageEmpty,
    WebPageNotModified,
)
from telethon.tl.types.messages import MessagesNotModified

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.repositories.logs import list_recent_logs
from core.telegram_client import execute
from core.telegram_client._warm_browse import _first_url, _pick_word
from schemas.telegram_actions import WarmLinkPreview, WarmSearchMessages
from tests.core.telegram_client.helpers import patch_action_client as _patch_client

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_URL = "https://example.com/a"


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


def _post(text: str, entities: list[object] | None = None, media: object = None) -> MagicMock:
    return MagicMock(message=text, entities=entities, media=media)


def _posts(*posts: object) -> MagicMock:
    return MagicMock(messages=list(posts))


def _search(channel: str = "@news", **kwargs: object) -> WarmSearchMessages:
    return WarmSearchMessages(
        channel=channel,
        message_ids=[10, 11],
        fallback_query="weather",
        **kwargs,  # ty: ignore[invalid-argument-type]
    )


# --- pickers ----------------------------------------------------------------------


def test_pick_word_skips_digits_short_words_and_punctuation() -> None:
    posts = [_post("Hi 1234 abc, x2y!"), _post("Hello, world!")]
    assert _pick_word(posts) == "Hello"


def test_pick_word_accepts_any_script() -> None:
    assert _pick_word([_post("Привет мир")]) == "Привет"


def test_pick_word_without_a_candidate_is_none() -> None:
    assert _pick_word([_post("a b 12345"), _post(""), MagicMock(message=None)]) is None


def test_first_url_reads_entity_text_link_and_preview_media_in_order() -> None:
    text_url = _post("read", [MessageEntityTextUrl(offset=0, length=4, url="https://a.example/")])
    page = MessageMediaWebPage(
        webpage=WebPage(id=1, url="https://b.example/", display_url="", hash=0)
    )
    assert _first_url([_post("bare", media=page)]) == "https://b.example/"
    assert _first_url([_post("nothing"), text_url]) == "https://a.example/"
    assert (
        _first_url([_post("see " + _URL, [MessageEntityUrl(offset=4, length=len(_URL))])]) == _URL
    )


def test_first_url_slices_entity_offsets_in_utf16_units() -> None:
    # The emoji is two UTF-16 units: a plain Python slice at offset 3 would eat a char.
    text = "\U0001f600 " + _URL
    assert _first_url([_post(text, [MessageEntityUrl(offset=3, length=len(_URL))])]) == _URL


def test_first_url_skips_invite_links_and_schemeless_text() -> None:
    posts = [
        _post("x", [MessageEntityTextUrl(offset=0, length=1, url="https://t.me/+abc")]),
        _post("x", [MessageEntityTextUrl(offset=0, length=1, url="https://t.me/joinchat/xyz")]),
        _post("x", [MessageEntityTextUrl(offset=0, length=1, url="tg://join?invite=q")]),
        _post("example.com", [MessageEntityUrl(offset=0, length=11)]),
        # Neither a bold run nor a preview stub without a URL yields anything.
        _post("bold", [MessageEntityBold(offset=0, length=4)]),
        _post("stub", media=MessageMediaWebPage(webpage=WebPageEmpty(id=1))),
        _post("x", [MessageEntityTextUrl(offset=0, length=1, url="https://c.example/")]),
    ]
    assert _first_url(posts) == "https://c.example/"
    assert _first_url(posts[:-1]) is None


# --- search messages --------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_messages_fetches_posts_then_searches_the_picked_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        {
            GetMessagesRequest: _posts(_post("Hi 1234 abc Weather today")),
            SearchRequest: MagicMock(messages=[1, 2, 3]),
        },
    )
    _patch_client(monkeypatch, client)

    result = await execute("acc-sm1", _search())

    assert result.status == "ok"
    assert client.entities == ["@news"]
    fetch, search = client.captured
    assert isinstance(fetch, GetMessagesRequest)
    assert fetch.channel == "peer:@news"
    assert [m.id for m in fetch.id] == [10, 11]  # ty: ignore[unresolved-attribute]
    assert all(isinstance(m, InputMessageID) for m in fetch.id)
    assert isinstance(search, SearchRequest)
    assert search.peer == "peer:@news"
    assert search.q == "Weather"
    assert len(search.q) >= 4
    assert isinstance(search.filter, InputMessagesFilterEmpty)
    assert search.hash == 0
    assert search.limit == 20
    assert (search.offset_id, search.add_offset, search.max_id, search.min_id) == (0, 0, 0, 0)
    extra = await _extra("telegram_warm_search_messages")
    assert extra["channel"] == "@news"
    assert extra["query_len"] == 7
    assert extra["found"] == 3
    assert extra["fallback"] is False
    assert extra["global"] is False
    assert "Weather" not in extra.values()


@pytest.mark.asyncio
async def test_search_messages_global_adds_one_global_search_and_sums_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        {
            GetMessagesRequest: _posts(_post("Weather")),
            SearchRequest: MagicMock(messages=[1]),
            SearchGlobalRequest: MagicMock(messages=[1, 2]),
        },
    )
    _patch_client(monkeypatch, client)

    result = await execute("acc-sm2", _search(global_search=True))

    assert result.status == "ok"
    _, _, global_search = client.captured
    assert isinstance(global_search, SearchGlobalRequest)
    assert global_search.q == "Weather"
    assert isinstance(global_search.filter, InputMessagesFilterEmpty)
    assert isinstance(global_search.offset_peer, InputPeerEmpty)
    assert (global_search.offset_rate, global_search.offset_id) == (0, 0)
    assert global_search.limit == 20
    extra = await _extra("telegram_warm_search_messages")
    assert extra["found"] == 3
    assert extra["global"] is True


@pytest.mark.asyncio
async def test_search_messages_falls_back_when_posts_have_no_usable_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = MessageEmpty(id=10, peer_id=PeerChannel(1))
    client = _FakeClient(
        {
            GetMessagesRequest: _posts(empty, _post("a b 12 345")),
            SearchRequest: MessagesNotModified(count=0),
        },
    )
    _patch_client(monkeypatch, client)

    result = await execute("acc-sm3", _search())

    assert result.status == "ok"
    _, search = client.captured
    assert search.q == "weather"  # ty: ignore[unresolved-attribute]
    extra = await _extra("telegram_warm_search_messages")
    assert extra["fallback"] is True
    assert extra["found"] == 0
    assert extra["query_len"] == 7


@pytest.mark.asyncio
async def test_search_messages_flood_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    flood = errors.FloodWaitError(request=None, capture=30)
    _patch_client(monkeypatch, _FakeClient({SearchRequest: flood}))

    result = await execute("acc-sm4", _search())

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 30


@pytest.mark.asyncio
async def test_search_messages_generic_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient({GetMessagesRequest: RuntimeError("x")}))

    result = await execute("acc-sm5", _search())

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"


# --- link preview -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_preview_opens_the_first_link_of_a_fetched_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        {
            GetMessagesRequest: _posts(
                _post("see " + _URL, [MessageEntityUrl(offset=4, length=21)])
            ),
            GetWebPageRequest: MagicMock(webpage=WebPageNotModified()),
        },
    )
    _patch_client(monkeypatch, client)

    result = await execute("acc-lp1", WarmLinkPreview(channel="@news", message_ids=[3]))

    assert result.status == "ok"
    fetch, preview = client.captured
    assert isinstance(fetch, GetMessagesRequest)
    assert [m.id for m in fetch.id] == [3]  # ty: ignore[unresolved-attribute]
    assert isinstance(preview, GetWebPageRequest)
    assert preview.url == _URL
    assert preview.url.startswith("https://")
    assert preview.hash == 0
    extra = await _extra("telegram_warm_link_preview")
    assert extra["channel"] == "@news"
    assert extra["webpage"] == "WebPageNotModified"
    assert _URL not in extra.values()


@pytest.mark.asyncio
async def test_link_preview_skips_when_posts_only_carry_invite_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invite = _post("join", [MessageEntityTextUrl(offset=0, length=4, url="https://t.me/+abc")])
    client = _FakeClient({GetMessagesRequest: _posts(invite, _post("plain text"))})
    _patch_client(monkeypatch, client)

    result = await execute("acc-lp2", WarmLinkPreview(channel="@news", message_ids=[3]))

    assert result.status == "ok"
    assert [type(r) for r in client.captured] == [GetMessagesRequest]
    assert (await _extra("telegram_warm_link_preview"))["warm_skip"] == "no_url"


@pytest.mark.parametrize(
    "exc",
    [
        errors.WebpageCurlFailedError(request=None),
        errors.WebpageMediaEmptyError(request=None),
        errors.UrlInvalidError(request=None),
    ],
    ids=lambda e: type(e).__name__,
)
@pytest.mark.asyncio
async def test_link_preview_unfetchable_page_is_a_skip(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
) -> None:
    client = _FakeClient(
        {
            GetMessagesRequest: _posts(_post(_URL, [MessageEntityUrl(offset=0, length=21)])),
            GetWebPageRequest: exc,
        },
    )
    _patch_client(monkeypatch, client)

    result = await execute("acc-lp3", WarmLinkPreview(channel="@news", message_ids=[3]))

    assert result.status == "ok"
    extra = await _extra("telegram_warm_link_preview")
    assert extra["warm_skip"] == "webpage_unavailable"
    assert "webpage" not in extra


@pytest.mark.asyncio
async def test_link_preview_flood_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    flood = errors.FloodWaitError(request=None, capture=45)
    client = _FakeClient(
        {
            GetMessagesRequest: _posts(_post(_URL, [MessageEntityUrl(offset=0, length=21)])),
            GetWebPageRequest: flood,
        },
    )
    _patch_client(monkeypatch, client)

    result = await execute("acc-lp4", WarmLinkPreview(channel="@news", message_ids=[3]))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 45


@pytest.mark.asyncio
async def test_link_preview_generic_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient({GetMessagesRequest: RuntimeError("x")}))

    result = await execute("acc-lp5", WarmLinkPreview(channel="@news", message_ids=[3]))

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"
