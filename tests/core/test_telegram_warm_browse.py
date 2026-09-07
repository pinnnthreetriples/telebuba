"""Tests for the browse-family warming reads (``core.telegram_client._warm_browse``).

Dialogs, the sticker and GIF panels, and the inline-bot query. Search and link
preview — the two that first fetch channel posts — live in ``test_telegram_warm_search``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.messages import (
    GetAllStickersRequest,
    GetDialogsRequest,
    GetFeaturedStickersRequest,
    GetInlineBotResultsRequest,
    GetRecentStickersRequest,
    GetSavedGifsRequest,
    GetStickerSetRequest,
)
from telethon.tl.types import InputPeerEmpty, InputPeerSelf, InputStickerSetID
from telethon.tl.types.messages import (
    AllStickersNotModified,
    DialogsNotModified,
    FeaturedStickersNotModified,
    SavedGifsNotModified,
)

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.repositories.logs import list_recent_logs
from core.telegram_client import execute
from schemas.telegram_actions import (
    WarmBrowseStickers,
    WarmGetDialogs,
    WarmInlineQuery,
    WarmSavedGifs,
)
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


def _featured(set_id: int = 7, access_hash: int = 9) -> MagicMock:
    return MagicMock(sets=[MagicMock(set=MagicMock(id=set_id, access_hash=access_hash))])


# --- dialogs ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dialogs_sends_raw_request_from_the_top(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient({GetDialogsRequest: MagicMock(dialogs=[object(), object(), object()])})
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
    _patch_client(monkeypatch, _FakeClient({GetDialogsRequest: DialogsNotModified(count=0)}))

    result = await execute("acc-d2", WarmGetDialogs())

    assert result.status == "ok"
    assert (await _extra("telegram_warm_get_dialogs"))["dialogs"] == 0


@pytest.mark.asyncio
async def test_get_dialogs_flood_wait_reaches_the_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    flood = errors.FloodWaitError(request=None, capture=17)
    _patch_client(monkeypatch, _FakeClient({GetDialogsRequest: flood}))

    result = await execute("acc-d3", WarmGetDialogs())

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 17


@pytest.mark.asyncio
async def test_get_dialogs_generic_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient({GetDialogsRequest: RuntimeError("boom")}))

    result = await execute("acc-d4", WarmGetDialogs())

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"


# --- stickers ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browse_stickers_opens_the_panel_and_views_one_featured_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        {
            GetAllStickersRequest: MagicMock(sets=[1, 2, 3]),
            GetFeaturedStickersRequest: _featured(set_id=7, access_hash=9),
            GetStickerSetRequest: MagicMock(documents=[1, 2]),
        },
    )
    _patch_client(monkeypatch, client)

    result = await execute("acc-st1", WarmBrowseStickers())

    assert result.status == "ok"
    assert [type(r) for r in client.captured] == [
        GetAllStickersRequest,
        GetRecentStickersRequest,
        GetFeaturedStickersRequest,
        GetStickerSetRequest,
    ]
    assert all(r.hash == 0 for r in client.captured)  # ty: ignore[unresolved-attribute]
    (set_req,) = [r for r in client.captured if isinstance(r, GetStickerSetRequest)]
    assert isinstance(set_req.stickerset, InputStickerSetID)
    assert set_req.stickerset.id == 7
    assert set_req.stickerset.access_hash == 9
    assert not [r for r in client.captured if "Install" in type(r).__name__]
    extra = await _extra("telegram_warm_browse_stickers")
    assert extra["installed"] == 3
    assert extra["featured"] == 1
    assert extra["set_documents"] == 2
    assert "warm_skip" not in extra


@pytest.mark.asyncio
async def test_browse_stickers_not_modified_answers_read_as_zero_and_skip_the_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        {
            GetAllStickersRequest: AllStickersNotModified(),
            GetFeaturedStickersRequest: FeaturedStickersNotModified(count=0),
        },
    )
    _patch_client(monkeypatch, client)

    result = await execute("acc-st2", WarmBrowseStickers())

    assert result.status == "ok"
    assert not [r for r in client.captured if isinstance(r, GetStickerSetRequest)]
    extra = await _extra("telegram_warm_browse_stickers")
    assert extra["installed"] == 0
    assert extra["featured"] == 0
    assert extra["set_documents"] == 0


@pytest.mark.asyncio
async def test_browse_stickers_gone_set_is_a_skip_not_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        {
            GetFeaturedStickersRequest: _featured(),
            GetStickerSetRequest: errors.StickersetInvalidError(request=None),
        },
    )
    _patch_client(monkeypatch, client)

    result = await execute("acc-st3", WarmBrowseStickers())

    assert result.status == "ok"
    extra = await _extra("telegram_warm_browse_stickers")
    assert extra["warm_skip"] == "set_gone"
    assert extra["set_documents"] == 0


@pytest.mark.asyncio
async def test_browse_stickers_flood_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    flood = errors.FloodWaitError(request=None, capture=12)
    _patch_client(monkeypatch, _FakeClient({GetRecentStickersRequest: flood}))

    result = await execute("acc-st4", WarmBrowseStickers())

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 12


@pytest.mark.asyncio
async def test_browse_stickers_generic_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient({GetAllStickersRequest: RuntimeError("x")}))

    result = await execute("acc-st5", WarmBrowseStickers())

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"


# --- saved gifs -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_saved_gifs_opens_the_tab_and_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient({GetSavedGifsRequest: MagicMock(gifs=[1, 2, 3, 4])})
    _patch_client(monkeypatch, client)

    result = await execute("acc-g1", WarmSavedGifs())

    assert result.status == "ok"
    (req,) = client.captured
    assert isinstance(req, GetSavedGifsRequest)
    assert req.hash == 0
    assert (await _extra("telegram_warm_saved_gifs"))["saved_gifs"] == 4


@pytest.mark.asyncio
async def test_saved_gifs_not_modified_counts_as_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient({GetSavedGifsRequest: SavedGifsNotModified()}))

    result = await execute("acc-g2", WarmSavedGifs())

    assert result.status == "ok"
    assert (await _extra("telegram_warm_saved_gifs"))["saved_gifs"] == 0


@pytest.mark.asyncio
async def test_saved_gifs_flood_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    flood = errors.FloodWaitError(request=None, capture=8)
    _patch_client(monkeypatch, _FakeClient({GetSavedGifsRequest: flood}))

    result = await execute("acc-g3", WarmSavedGifs())

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 8


@pytest.mark.asyncio
async def test_saved_gifs_generic_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient({GetSavedGifsRequest: RuntimeError("x")}))

    result = await execute("acc-g4", WarmSavedGifs())

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"


# --- inline query -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_inline_query_resolves_the_bot_then_asks_it_from_saved_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient({GetInlineBotResultsRequest: MagicMock(results=[1, 2])})
    _patch_client(monkeypatch, client)

    result = await execute("acc-i1", WarmInlineQuery(bot="pic", query="cats"))

    assert result.status == "ok"
    # The bot comes off the session entity cache — no raw ``contacts.resolveUsername``.
    assert client.entities == ["pic"]
    (ask,) = client.captured
    assert isinstance(ask, GetInlineBotResultsRequest)
    assert ask.bot == "peer:pic"
    assert isinstance(ask.peer, InputPeerSelf)
    assert ask.query == "cats"
    assert ask.offset == ""
    assert ask.geo_point is None
    extra = await _extra("telegram_warm_inline_query")
    assert extra["bot"] == "pic"
    assert extra["results"] == 2
    assert extra["query_len"] == 4
    assert "cats" not in extra.values()


@pytest.mark.asyncio
async def test_inline_query_refuses_a_bot_outside_the_whitelist_before_any_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    _patch_client(monkeypatch, client)

    result = await execute("acc-i2", WarmInlineQuery(bot="evilbot", query="cats"))

    assert result.status == "failed"
    assert result.error_type == "ValueError"
    assert client.captured == []
    assert client.entities == []


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (errors.BotResponseTimeoutError(request=None), "bot_timeout"),
        (errors.TimeoutError(request=None), "bot_timeout"),
        (errors.BotInlineDisabledError(request=None), "bot_invalid"),
    ],
    ids=["bot_response_timeout", "rpc_timeout_503", "inline_disabled"],
)
@pytest.mark.asyncio
async def test_inline_query_bot_faults_are_skips(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    code: str,
) -> None:
    client = _FakeClient({GetInlineBotResultsRequest: exc})
    _patch_client(monkeypatch, client)

    result = await execute("acc-i3", WarmInlineQuery(bot="wiki", query="rivers"))

    assert result.status == "ok"
    extra = await _extra("telegram_warm_inline_query")
    assert extra["warm_skip"] == code
    assert "results" not in extra


@pytest.mark.asyncio
async def test_inline_query_flood_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    flood = errors.FloodWaitError(request=None, capture=40)
    client = _FakeClient({GetInlineBotResultsRequest: flood})
    _patch_client(monkeypatch, client)

    result = await execute("acc-i4", WarmInlineQuery(bot="gif", query="dogs"))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 40


@pytest.mark.asyncio
async def test_inline_query_generic_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient({GetInlineBotResultsRequest: RuntimeError("x")}))

    result = await execute("acc-i5", WarmInlineQuery(bot="vid", query="dogs"))

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"
