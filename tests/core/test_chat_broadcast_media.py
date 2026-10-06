"""The chat-broadcast photo store: content-addressed, decodable images only."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from core.chat_broadcast_media import BroadcastMediaError, read_photo, store_photo
from core.config import settings

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _media_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.chat_broadcast, "media_dir", tmp_path / "media")


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_store_is_idempotent_and_reads_back() -> None:
    content = _png()

    first = await store_photo(content, ".png")
    second = await store_photo(content, ".png")

    assert first == second
    assert first.endswith(".png")
    assert await read_photo(first) == content


@pytest.mark.asyncio
@pytest.mark.parametrize(("content", "suffix"), [(b"not an image", ".png"), (b"x", ".gif")])
async def test_store_refuses_what_is_not_a_photo(content: bytes, suffix: str) -> None:
    with pytest.raises(BroadcastMediaError, match="media_invalid"):
        await store_photo(content, suffix)


@pytest.mark.asyncio
async def test_read_refuses_bad_names_missing_and_tampered_files(tmp_path: Path) -> None:
    media_id = await store_photo(_png(), ".png")
    with pytest.raises(BroadcastMediaError, match="media_not_found"):
        await read_photo("../etc/passwd")
    with pytest.raises(BroadcastMediaError, match="media_not_found"):
        await read_photo("0" * 64 + ".png")
    (tmp_path / "media" / media_id).write_bytes(b"tampered")
    with pytest.raises(BroadcastMediaError, match="media_not_found"):
        await read_photo(media_id)
