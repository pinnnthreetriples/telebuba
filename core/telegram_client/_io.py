"""Shared in-memory upload helpers for the Telegram client."""

from __future__ import annotations

from io import BytesIO


def _named_bytes(filename: str, content: bytes) -> BytesIO:
    stream = BytesIO(content)
    stream.name = filename
    return stream
