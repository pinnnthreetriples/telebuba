"""Tests for ``download_post_image`` — the caption-less-photo fetch behind the vision path.

Every arm returns a typed "no image, here is why" instead of raising: the caller's only
fallback is the post-skip it was already doing, so a crash here would kill a listener
task over a picture.

The photos here are REAL ``telethon.tl.types`` objects and the fake client resolves the
requested size through Telethon's own ``DownloadMethods._get_thumb``, because the size
gate's whole job is to agree with that resolution: a hand-set ``file.size`` on a
``SimpleNamespace`` would pass while the shipped code pulled something else entirely.
"""

from __future__ import annotations

import asyncio
import base64
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon.client.downloads import DownloadMethods
from telethon.tl.custom.file import File
from telethon.tl.types import (
    MessageMediaDocument,
    MessageMediaPhoto,
    Photo,
    PhotoCachedSize,
    PhotoSize,
    PhotoSizeProgressive,
    PhotoStrippedSize,
    TypePhotoSize,
    TypeVideoSize,
    VideoSize,
)

from core.telegram_client import _read_post_image
from core.telegram_client._read_post_image import download_post_image

if TYPE_CHECKING:
    from schemas.telegram_actions import PostImageResult

_MAX_BYTES = 1000


def _photo_media(
    *sizes: TypePhotoSize,
    video_sizes: list[TypeVideoSize] | None = None,
) -> MessageMediaPhoto:
    """A real ``MessageMediaPhoto``, so ``File.size`` and ``_get_thumb`` behave for real."""
    return MessageMediaPhoto(
        photo=Photo(
            id=1,
            access_hash=2,
            file_reference=b"ref",
            date=None,
            sizes=list(sizes),
            dc_id=2,
            video_sizes=list(video_sizes or []),
        ),
    )


def _declared_bytes(size: object) -> int:
    """What Telegram says this size weighs — the number the gate has to match."""
    if isinstance(size, PhotoSizeProgressive):
        return max(size.sizes)
    if isinstance(size, (PhotoSize, VideoSize)):
        return size.size
    if isinstance(size, (PhotoCachedSize, PhotoStrippedSize)):
        return len(size.bytes)
    raise AssertionError(size)


# Sentinel for "the download answered with nothing at all", which ``None`` cannot say
# here because ``None`` already means "answer honestly from the picked size".
_NOTHING = object()


class _FakeClient:
    """Serves a preset message, and downloads whatever Telethon's own picker chooses.

    ``download_media`` mirrors ``TelegramClient._download_photo``: it resolves ``thumb``
    against ``photo.sizes + photo.video_sizes`` through the real ``_get_thumb`` and hands
    back exactly that many bytes. ``lies_with`` overrides the answer, to fake a size that
    under-reports itself or a download that yields nothing.
    """

    def __init__(self, message: object, *, lies_with: object = None) -> None:
        self._message = message
        self._lies_with = lies_with
        self.downloads = 0
        self.requested_thumbs: list[object] = []

    async def get_messages(self, channel: str, *, ids: int) -> object:
        assert (channel, ids) == ("@chan", 42)
        return self._message

    async def download_media(
        self, message: SimpleNamespace, *, file: object, thumb: object = None
    ) -> object:
        assert file is bytes  # in-memory download, not a path
        self.downloads += 1
        self.requested_thumbs.append(thumb)
        if self._lies_with is _NOTHING:
            return None
        if self._lies_with is not None:
            return self._lies_with
        photo = message.media.photo
        picked = DownloadMethods._get_thumb(photo.sizes + (photo.video_sizes or []), thumb)
        return b"x" * _declared_bytes(picked)


def _message(media: object) -> SimpleNamespace:
    file = File(media.photo) if isinstance(media, MessageMediaPhoto) else None
    return SimpleNamespace(media=media, file=file)


def _patch_pool(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    async def _get_client(account_id: str) -> object:
        assert account_id == "acc-1"
        return client

    monkeypatch.setattr(_read_post_image, "get_client", _get_client)


async def _download(max_bytes: int = _MAX_BYTES) -> PostImageResult:
    return await download_post_image("acc-1", "@chan", 42, max_bytes)


@pytest.mark.asyncio
async def test_photo_comes_back_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pool(monkeypatch, _FakeClient(_message(_photo_media(PhotoSize("y", 1, 1, 100)))))
    result = await _download()
    assert result.image_b64 == base64.b64encode(b"x" * 100).decode("ascii")
    assert result.reason is None


@pytest.mark.asyncio
@pytest.mark.parametrize("media", [None, MagicMock(spec=MessageMediaDocument)])
async def test_post_without_a_photo_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, media: object
) -> None:
    client = _FakeClient(_message(media))
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


# --------------------------------------------------------------------------- #
# The gate must measure the bytes it is about to pull — no other list
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("video_bytes", [900, 5000])
async def test_animated_photo_yields_the_still_never_the_video(
    monkeypatch: pytest.MonkeyPatch, video_bytes: int
) -> None:
    """A ``VideoSize`` outranks every still in Telethon's picker but not in ``file.size``.

    So a caption-less "animated" photo cleared the gate on its 400-byte still and then
    pulled the MP4 — under the cap it reached the model mislabelled ``image/jpeg``, over
    it the bytes were in RAM before anything noticed. Both are the same defect.
    """
    media = _photo_media(
        PhotoSize("y", 1, 1, 400),
        video_sizes=[VideoSize("v", 1, 1, video_bytes)],
    )
    client = _FakeClient(_message(media))
    _patch_pool(monkeypatch, client)

    result = await _download()

    assert result.reason is None
    assert result.image_b64 is not None
    assert len(base64.b64decode(result.image_b64)) == 400
    # Named, not left to the picker: the still's own type is what was asked for.
    assert client.requested_thumbs == ["y"]


@pytest.mark.asyncio
async def test_oversized_original_falls_back_to_the_size_that_fits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 3 KB original must not cost the post its comment when an 800-byte sibling fits."""
    media = _photo_media(
        PhotoSize("x", 1, 1, 150),
        PhotoSize("y", 1, 1, 900),
        PhotoSize("w", 1, 1, 3000),
    )
    client = _FakeClient(_message(media))
    _patch_pool(monkeypatch, client)

    result = await _download()

    assert result.reason is None
    assert result.image_b64 is not None
    assert len(base64.b64decode(result.image_b64)) == 900
    assert client.requested_thumbs == ["y"]


@pytest.mark.asyncio
async def test_photo_whose_every_size_is_over_the_cap_is_refused_before_the_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = _photo_media(PhotoSize("x", 1, 1, 2000), PhotoSize("y", 1, 1, 5000))
    client = _FakeClient(_message(media))
    _patch_pool(monkeypatch, client)

    result = await _download()

    assert (result.image_b64, result.reason) == (None, "too_large")
    # The whole point of reading the size off the metadata: no bytes were pulled.
    assert client.downloads == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("size", "expected"),
    [
        # Progressive weighs its LAST layer, not its first — gating on 100 would have
        # under-read this one by 30x.
        (PhotoSizeProgressive("y", 1, 1, [100, 400, 3000]), "too_large"),
        (PhotoSizeProgressive("y", 1, 1, [100, 400, 900]), None),
        (PhotoStrippedSize("i", b"z" * 2000), "too_large"),
        (PhotoStrippedSize("i", b"z" * 50), None),
        (PhotoCachedSize("a", 1, 1, b"z" * 2000), "too_large"),
        (PhotoCachedSize("a", 1, 1, b"z" * 50), None),
    ],
)
async def test_every_still_size_class_is_weighed_by_its_own_rule(
    monkeypatch: pytest.MonkeyPatch, size: TypePhotoSize, expected: str | None
) -> None:
    client = _FakeClient(_message(_photo_media(size)))
    _patch_pool(monkeypatch, client)
    result = await _download()
    assert result.reason == expected


@pytest.mark.asyncio
async def test_a_size_that_under_reports_itself_is_caught_after_the_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The belt behind the gate: metadata said 100 bytes, the wire delivered 2000.
    client = _FakeClient(
        _message(_photo_media(PhotoSize("y", 1, 1, 100))),
        lies_with=b"x" * (_MAX_BYTES + 1),
    )
    _patch_pool(monkeypatch, client)
    result = await _download()
    assert (result.image_b64, result.reason) == (None, "too_large")


@pytest.mark.asyncio
async def test_download_yielding_no_bytes_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(_message(_photo_media(PhotoSize("y", 1, 1, 100))), lies_with=_NOTHING)
    _patch_pool(monkeypatch, client)
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


# --------------------------------------------------------------------------- #
# The wall-clock bound — a slow proxy, not a dead one
# --------------------------------------------------------------------------- #


class _SlowClient(_FakeClient):
    """Answers, but only long after the fetch budget is gone (a crawling proxy)."""

    async def download_media(
        self, message: SimpleNamespace, *, file: object, thumb: object = None
    ) -> object:
        await asyncio.sleep(5)
        return await super().download_media(message, file=file, thumb=thumb)


@pytest.mark.asyncio
async def test_a_crawling_download_is_cut_off_and_reported_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telethon pulls a photo in sequential 128 KB parts with no deadline of its own.

    A proxy that answers slowly rather than not at all can stretch that past the
    stale-claim cutoff, at which point the sweep fails the row under a live worker and
    the comment it later delivers is silently dropped. The bound has to be real.
    """
    # ``raising=False``: against a build with no bound at all this leaves the fetch
    # unbounded, so the test fails by DELIVERING the picture five seconds late
    # instead of by not finding a constant to shrink.
    monkeypatch.setattr(_read_post_image, "PHOTO_FETCH_TIMEOUT_SECONDS", 0.05, raising=False)
    _patch_pool(monkeypatch, _SlowClient(_message(_photo_media(PhotoSize("y", 1, 1, 100)))))

    started = time.monotonic()
    result = await _download()

    assert (result.image_b64, result.reason) == (None, "unavailable")
    assert time.monotonic() - started < 1.0


@pytest.mark.asyncio
async def test_a_hung_pool_acquisition_is_cut_off_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bound covers the whole fetch, not just the bytes: connecting can hang as well."""

    async def _slow_client(_account_id: str) -> object:
        await asyncio.sleep(5)
        raise AssertionError

    # ``raising=False``: against a build with no bound at all this leaves the fetch
    # unbounded, so the test fails by DELIVERING the picture five seconds late
    # instead of by not finding a constant to shrink.
    monkeypatch.setattr(_read_post_image, "PHOTO_FETCH_TIMEOUT_SECONDS", 0.05, raising=False)
    monkeypatch.setattr(_read_post_image, "get_client", _slow_client)

    started = time.monotonic()
    result = await _download()

    assert (result.image_b64, result.reason) == (None, "unavailable")
    assert time.monotonic() - started < 1.0
