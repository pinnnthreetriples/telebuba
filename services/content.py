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

# Telethon's ``markdown.DEFAULT_DELIMITERS`` (verified against the installed
# 1.44.0), longest first exactly as its own ``sorted(key=len, reverse=True)`` does,
# so ``` is not read as a single backtick. Each alternative captures the ENCLOSED
# span, because Telethon only consumes a delimiter that has a PARTNER:
# ``message.find(delim, i + len(delim) + 1)``, and leaves it literal when there is
# none. ``.+?`` reproduces both halves of that rule — pairing, and the ≥1-character
# span the ``+ 1`` offset requires. DOTALL because Telethon's ``find`` spans lines.
# The single-backtick arm additionally refuses a backtick as its first content
# character. Telethon tries only the delimiters that match AT the position it is
# on and never falls back to a shorter one there, so an unclosed ``` fence stays
# literal; a plain ``` `(.+?)` ``` would instead pair the first and third backtick
# across the second and turn ```` ```py ```` into ```` `py ````.
_MARKDOWN_PAIR_RE = re.compile(
    r"```(.+?)```|\*\*(.+?)\*\*|__(.+?)__|~~(.+?)~~|`([^`].*?)`",
    re.DOTALL,
)
# Telethon's ``markdown.DEFAULT_URL_RE``, verbatim. The parser consumed this form
# too — ``[text](url)`` went out as ``text`` plus a MessageEntityTextUrl.
_MARKDOWN_LINK_RE = re.compile(r"\[([^]]*?)\]\(([\s\S]*?)\)")


def _enclosed_span(match: re.Match[str]) -> str:
    """The one group that participated — the text between a matched delimiter pair."""
    return next(group for group in match.groups() if group is not None)


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
    formatting, so an appended request is not something we can rely on.

    PAIRED delimiters only, and the link form as well — both because the target is
    "what Telethon would have consumed", not "every delimiter character". A blind
    substitution got that wrong in both directions: it deleted UNPAIRED markers
    Telethon leaves alone, corrupting ``snake_case__name`` into ``snake_casename``,
    eating a stray backtick and mangling a URL containing ``__``; and it left
    ``[тут](tg://user?id=1)`` to post literally, since ``__`` and friends are not
    the only syntax the parser ate. (``has_link`` rejects ``https?://`` and ``t.me``
    but not ``tg://``, so that one really did reach a comment.) The label is kept
    and the target dropped, which is what a reader used to see.

    The loop re-runs until stable so a nested pair (``**a `b` c**``) comes out fully
    bare — matching Telethon, which leaves ``i`` where it was and re-examines the
    span it just unwrapped. It terminates because every pass that matches removes at
    least two characters. One deliberate divergence: Telethon skips PAST a code span
    ("no nested entities inside code blocks"), so it renders ```` ``py`` ```` as
    `` `py` `` where this returns ``py``. Stripping the extra pair only removes a
    visible marker, never a word, which is the direction this function exists to err
    in — pinned in the tests so it stays a decision.
    """
    stripped = _MARKDOWN_LINK_RE.sub(r"\1", text)
    while (unwrapped := _MARKDOWN_PAIR_RE.sub(_enclosed_span, stripped)) != stripped:
        stripped = unwrapped
    return stripped


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
