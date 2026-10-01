"""Helpers for the scheduled-post service tests."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from PIL import Image

from core import scheduled_media
from core.db import create_account
from schemas.accounts import AccountCreate

if TYPE_CHECKING:
    from pathlib import Path


def png_bytes(color: str = "red", size: tuple[int, int] = (64, 64)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


async def stored_image(tmp_path: Path, color: str = "red") -> str:
    """Put one PNG into the store and answer its media id."""
    source = tmp_path / f"{color}.png"
    source.write_bytes(png_bytes(color))
    return (await scheduled_media.ingest(source, ".png")).name


async def stored_video(tmp_path: Path) -> str:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"\x00\x00\x00\x18ftypmp42 not really a video")
    return (await scheduled_media.ingest(source, ".mp4")).name


async def seed_account(account_id: str = "acc") -> None:
    await create_account(AccountCreate(account_id=account_id))


def in_minutes(minutes: float) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes)
