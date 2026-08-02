"""Tests for ``download_post_image`` — the caption-less-photo fetch behind the vision path.

Every arm returns a typed "no image, here is why" instead of raising: the caller's only
fallback is the post-skip it was already doing, so a crash here would kill a listener
task over a picture.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto

from core.telegram_client import _read_post_image
from core.telegram_client._read_post_image import download_post_image

if TYPE_CHECKING:
    from schemas.telegram_actions import PostImageResult

_MAX_BYTES = 1000


class _FakeClient:
    """Returns a preset message from ``get_messages`` and preset bytes from the download."""

    def __init__(self, message: object, data: object = b"jpeg-bytes") -> None:
        self._message = message
        self._data = data
        self.downloads = 0

    async def get_messages(self, channel: str, *, ids: int) -> object:
        assert (channel, ids) == ("@chan", 42)
        return self._message

    async def download_media(self, _message: object, *, file: object) -> object:
        assert file is bytes  # in-memory download, not a path
        self.downloads += 1
        return self._data


def _message(*, media: object, size: int | None = 100) -> SimpleNamespace:
    return SimpleNamespace(media=media, file=SimpleNamespace(size=size))


def _patch_pool(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    async def _get_client(account_id: str) -> object:
        assert account_id == "acc-1"
        return client

    monkeypatch.setattr(_read_post_image, "get_client", _get_client)


async def _download() -> PostImageResult:
    return await download_post_image("acc-1", "@chan", 42, _MAX_BYTES)


@pytest.mark.asyncio
async def test_photo_comes_back_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pool(monkeypatch, _FakeClient(_message(media=MagicMock(spec=MessageMediaPhoto))))
    result = await _download()
    assert result.image_b64 == base64.b64encode(b"jpeg-bytes").decode("ascii")
    assert result.reason is None


@pytest.mark.asyncio
@pytest.mark.parametrize("media", [None, MagicMock(spec=MessageMediaDocument)])
async def test_post_without_a_photo_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, media: object
) -> None:
    client = _FakeClient(_message(media=media))
    _patch_pool(monkeypatch, client)
    result = await _download()
    assert (result.image_b64, result.reason) == (None, "unavailable")
    assert client.downloads == 0


@pytest.mark.asyncio
async def test_deleted_post_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # ``get_messages(ids=<int>)`` yields None for a message that is gone / invisible.
    _patch_pool(monkeypatch, _FakeClient(None))
    result = await _download()
    assert (result.image_b64, result.reason) == (None, "unavailable")


@pytest.mark.asyncio
async def test_oversized_photo_is_refused_before_it_is_downloaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(_message(media=MagicMock(spec=MessageMediaPhoto), size=_MAX_BYTES + 1))
    _patch_pool(monkeypatch, client)
    result = await _download()
    assert (result.image_b64, result.reason) == (None, "too_large")
    # The whole point of reading the size off the metadata: no bytes were pulled.
    assert client.downloads == 0


@pytest.mark.asyncio
async def test_oversized_photo_without_metadata_is_caught_after_the_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No usable ``file.size`` → the pre-check can't fire; the post-download re-check must.
    client = _FakeClient(
        _message(media=MagicMock(spec=MessageMediaPhoto), size=None), b"x" * (_MAX_BYTES + 1)
    )
    _patch_pool(monkeypatch, client)
    result = await _download()
    assert (result.image_b64, result.reason) == (None, "too_large")


@pytest.mark.asyncio
async def test_download_yielding_no_bytes_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pool(monkeypatch, _FakeClient(_message(media=MagicMock(spec=MessageMediaPhoto)), None))
    result = await _download()
    assert (result.image_b64, result.reason) == (None, "unavailable")


@pytest.mark.asyncio
async def test_gateway_fault_is_swallowed_into_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flood wait / dead proxy / pool failure must not escape and kill the post task."""

    async def _boom(_account_id: str) -> object:
        msg = "proxy is down"
        raise ConnectionError(msg)

    monkeypatch.setattr(_read_post_image, "get_client", _boom)

    result = await _download()

    assert (result.image_b64, result.reason) == (None, "unavailable")
