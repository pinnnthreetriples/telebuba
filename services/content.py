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
from core.db import record_sent_hash, release_sent_hash, try_reserve_sent_hash

_LINK_RE = re.compile(r"(https?://|www\.|t\.me/|telegram\.me/)", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")

# Exactly Telethon's ``markdown.DEFAULT_DELIMITERS`` (1.44): ``**`` bold, ``__``
# italic, ``~~`` strike, backtick code — ``` collapses because each backtick
# matches. A lone ``*`` was never a delimiter, so it stays literal.
_MARKDOWN_DELIMITER_RE = re.compile(r"\*\*|__|~~|`")


def strip_markdown_delimiters(text: str) -> str:
    """Drop the markdown markers Telethon's parser used to consume.

    ``parse_mode`` is disabled on every client (``core.telegram_client._client``),
    which is the point: an operator's channel post must go out exactly as typed.
    But the same change made an LLM's ``**Отличный пост!**`` arrive as a channel
    comment with the asterisks visible — a machine tell on the two surfaces whose
    whole job is to look human, where it used to render as bold.

    So this is applied to GENERATED text only (the warming chat line and the
    neurocomment candidate), never to operator-authored text. Deterministic rather
    than a "no markdown" prompt instruction: the neurocomment instruction is
    prefixed by the operator's own ``campaign.prompt``, which is free to ask for
    formatting, so an appended request is not something we can rely on. Links are
    a separate concern — :func:`has_link` already rejects them.
    """
    return _MARKDOWN_DELIMITER_RE.sub("", text)


def normalize_text(text: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace for hashing."""
    stripped = _PUNCT_RE.sub("", text.casefold())
    return _WS_RE.sub(" ", stripped).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def similarity(a: str, b: str) -> float:
    """Token-set Jaccard over normalized text: intersection size / union size, 0.0-1.0.

    A cheap, local near-duplicate signal for cross-account comment dedup — no
    embeddings, no network on the hot path. Two empty token sets count as identical.
    """
    tokens_a = set(normalize_text(a).split())
    tokens_b = set(normalize_text(b).split())
    union = tokens_a | tokens_b
    if not union:
        return 1.0
    return len(tokens_a & tokens_b) / len(union)


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


async def release_sent_text(text: str) -> None:
    """Release a previously-reserved sent-text hash (P2.6).

    Pair with :func:`try_reserve_sent` on a send-failure path: the dedup
    reservation we took to gate concurrent senders must be dropped so the
    next retry of the same text isn't filtered as a duplicate. With a zero
    dedup window try_reserve_sent never touched the store, so this is a no-op.
    """
    window = settings.warming.content_dedup_window_days
    if window <= 0:
        return
    await release_sent_hash(content_hash(text))
