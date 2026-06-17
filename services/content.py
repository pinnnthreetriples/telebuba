"""Outbound-content guards: de-duplication and a link/forbidden-word filter.

Pure text helpers plus a thin TTL-window dedup over the sent-message hash store.
Identical content sent repeatedly (especially across accounts) is a strong spam
signal, so the warming/dialogue engines run generated text through here before
sending. No Telethon, no SQLAlchemy — DB access goes through ``core.db``.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta

from core.config import settings
from core.db import record_sent_hash, try_reserve_sent_hash

_LINK_RE = re.compile(r"(https?://|www\.|t\.me/|telegram\.me/)", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace for hashing."""
    stripped = _PUNCT_RE.sub("", text.casefold())
    return _WS_RE.sub(" ", stripped).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def has_link(text: str) -> bool:
    return _LINK_RE.search(text) is not None


def has_forbidden_word(text: str, words: list[str]) -> bool:
    lowered = text.casefold()
    return any(word.casefold() in lowered for word in words)


def is_acceptable(text: str) -> bool:
    """True when the text passes the outbound filter (no links/forbidden words)."""
    warm = settings.warming
    if warm.content_block_links and has_link(text):
        return False
    return not has_forbidden_word(text, warm.content_forbidden_words)


async def register_sent(text: str) -> None:
    """Record that this text has been sent (for future dedup).

    Prefer :func:`try_reserve_sent` when the call is the gate before a send —
    that variant is atomic. ``register_sent`` is the no-op-on-failure fallback
    used by code paths that have already established uniqueness.
    """
    await record_sent_hash(content_hash(text))


async def try_reserve_sent(text: str) -> bool:
    """Atomically claim a content hash before sending — True if claim wins.

    Combines checking and registration into a single
    transaction so two concurrent senders of the same text cannot both pass
    the dedup gate. A False return means another sender already reserved this
    text within the dedup window; the caller must abort.
    """
    window = settings.warming.content_dedup_window_days
    if window <= 0:
        await register_sent(text)
        return True
    since = (datetime.now(UTC) - timedelta(days=window)).isoformat()
    return await try_reserve_sent_hash(content_hash(text), since)
