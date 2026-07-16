"""Unit tests for the extracted profile read helpers (``_read_profile.py``).

The dispatcher-level flows live in ``test_telegram_read.py`` (via
``execute_read``); these cover the helper fallback branches directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from core.telegram_client._read_profile import (
    _optional_str,
    _photo_date_unix,
    dispatch_list_profile_music,
)
from schemas.telegram_profile_snapshot import TelegramProfileMusic


def test_optional_str_none_blank_and_padded() -> None:
    assert _optional_str(None) is None
    assert _optional_str("   ") is None
    assert _optional_str(" x ") == "x"


def test_photo_date_unix_covers_every_date_shape() -> None:
    """An int passes through; a datetime flattens; everything else degrades to 0."""
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    assert _photo_date_unix(SimpleNamespace(date=moment)) == int(moment.timestamp())
    assert _photo_date_unix(SimpleNamespace(date=1_750_000_000)) == 1_750_000_000
    # No date attribute at all.
    assert _photo_date_unix(SimpleNamespace()) == 0
    # A date without a callable .timestamp (e.g. a raw string).
    assert _photo_date_unix(SimpleNamespace(date="not-a-date")) == 0

    class BadTimestamp:
        def timestamp(self) -> float:
            msg = "broken clock"
            raise ValueError(msg)

    # A .timestamp() that raises must degrade, not propagate.
    assert _photo_date_unix(SimpleNamespace(date=BadTimestamp())) == 0


class _FakeMusicRequest:
    def __init__(self, *, id: object, offset: int, limit: int, hash: int) -> None:  # noqa: A002 — mirrors Telethon's TL parameter
        self.id = id
        self.offset = offset
        self.limit = limit
        self.hash = hash


@pytest.mark.asyncio
async def test_list_profile_music_skips_idless_docs_and_non_audio_attributes() -> None:
    """A zero-id document is dropped; a doc without an audio attribute maps to None fields."""

    class FakeClient:
        async def __call__(self, request: object) -> object:
            assert isinstance(request, _FakeMusicRequest)
            return SimpleNamespace(
                documents=[
                    # Placeholder without an id — must be skipped entirely.
                    SimpleNamespace(id=0),
                    # Real doc whose attributes hold no DocumentAttributeAudio.
                    SimpleNamespace(
                        id=5,
                        attributes=[SimpleNamespace()],
                        access_hash=1,
                        file_reference=b"\x01",
                    ),
                ],
            )

    result = await dispatch_list_profile_music(FakeClient(), _FakeMusicRequest)  # ty: ignore[invalid-argument-type]

    assert isinstance(result, TelegramProfileMusic)
    assert result.supported is True
    assert [track.file_id for track in result.items] == [5]
    assert result.items[0].title is None
    assert result.items[0].performer is None
    assert result.items[0].duration_seconds is None
