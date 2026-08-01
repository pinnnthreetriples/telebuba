"""The warming react action: emoji selection plus the dispatch that places one.

Split out of :mod:`core.telegram_client._actions` to keep that module under the
file-size cap; ``_actions`` keeps the ``match`` arm and delegates the body here.
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING

from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ChatReactionsNone, ChatReactionsSome, ReactionEmoji

from core.config import settings
from core.telegram_client._action_results import _DispatchResult

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import ReactToPost

# SystemRandom: non-cryptographic selection, but avoids the module-level
# `random.*` calls that ruff S311 flags. Behaviour is identical for our needs.
_rng = random.SystemRandom()

# A channel's allowed-reaction set changes rarely, but the react action re-read it
# on every reaction. Cache it per channel for an hour. Single event loop / one
# uvicorn worker (CLAUDE.md) → no lock needed. Failures are not cached so a
# transient error re-probes next time rather than sticking a bad "None".
_WHITELIST_TTL_SECONDS = 3600.0
_whitelist_cache: dict[str, tuple[float, set[str] | None]] = {}


async def _channel_reaction_whitelist(client: TelegramClient, channel: str) -> set[str] | None:
    """Emoticons the channel permits as reactions.

    ``None`` means "don't filter" — the channel allows all emoji (or the
    availability couldn't be read, in which case we fall back to the caller's
    default set rather than regress). An empty set means reactions are off or the
    channel only permits emoji we don't use, so the caller should skip entirely.
    """
    now = time.monotonic()
    cached = _whitelist_cache.get(channel)
    if cached is not None and now - cached[0] < _WHITELIST_TTL_SECONDS:
        return cached[1]
    try:
        full = await client(GetFullChannelRequest(channel=channel))  # ty: ignore[invalid-argument-type]
    except Exception:  # noqa: BLE001 - availability is best-effort; don't fail the react over it.
        return None  # transient — don't cache the failure.
    available = getattr(getattr(full, "full_chat", None), "available_reactions", None)
    if isinstance(available, ChatReactionsNone):
        result: set[str] | None = set()
    elif isinstance(available, ChatReactionsSome):
        result = {r.emoticon for r in available.reactions if isinstance(r, ReactionEmoji)}
    else:
        # ChatReactionsAll / unknown → any emoji is accepted, so don't narrow.
        result = None
    _whitelist_cache[channel] = (now, result)
    return result


def _bare_emoji(emoji: str) -> str:
    """Telegram's reaction emoticons omit the U+FE0F variation selector.

    Our configured set may carry it (e.g. ``"❤️"``); strip it so comparisons and
    the emoji we send line up with the channel's canonical form (``"❤"``).
    """
    return emoji.replace("\N{VARIATION SELECTOR-16}", "")


def _pick_reaction(preferred: list[str], allowed: set[str] | None) -> str | None:
    """Choose an emoticon to react with (bare form), or ``None`` to skip.

    ``allowed is None`` → the channel accepts any emoji, so use our configured
    set. Otherwise react with one of *our* emoji the channel permits; when none
    overlap, fall back to any non-negative emoji the channel does permit so a
    reaction still lands on restrictive channels (e.g. @durov). Returns ``None``
    only when the channel offers no usable emoji at all.
    """
    if allowed is None:
        pool = [_bare_emoji(e) for e in preferred]
        return _rng.choice(pool) if pool else None
    allowed_bare = {_bare_emoji(e) for e in allowed}
    ours = [e for e in (_bare_emoji(p) for p in preferred) if e in allowed_bare]
    if ours:
        return _rng.choice(ours)
    negatives = {_bare_emoji(e) for e in settings.warming.reaction_negative_emoji}
    fallback = [e for e in allowed_bare if e not in negatives]
    return _rng.choice(fallback) if fallback else None


async def dispatch_react_to_post(client: TelegramClient, action: ReactToPost) -> _DispatchResult:
    """React to a random recent post with an emoji the channel actually permits.

    Picking blindly from the configured set trips ``ReactionInvalidError`` on
    channels that restrict reactions (e.g. @durov). We first read the channel's
    allowed set and prefer one of our emoji from it; if none overlap we still
    react with one of the channel's own (non-negative) emoji so a reaction lands.
    The outcome always rides back in ``log_extra`` so the activity log can show
    it: the placed emoji on success, or a ``reaction_skip`` reason (no recent
    posts / no usable emoji) when nothing landed.
    """
    if action.message_ids is None:
        messages = await client.get_messages(action.channel, limit=action.message_limit)
        candidates = [
            int(getattr(m, "id", 0))
            for m in messages  # ty: ignore[not-iterable]
            if getattr(m, "id", None)
        ]
    else:
        candidates = action.message_ids
    if not candidates:
        return _DispatchResult(log_extra={"reaction_skip": "no_posts"})
    allowed = await _channel_reaction_whitelist(client, action.channel)
    emoji = _pick_reaction(action.reactions, allowed)
    if emoji is None:
        return _DispatchResult(log_extra={"reaction_skip": "no_emoji"})
    message_id = _rng.choice(candidates)
    peer = await client.get_input_entity(action.channel)
    await client(
        SendReactionRequest(
            peer=peer,
            msg_id=message_id,
            reaction=[ReactionEmoji(emoticon=emoji)],
        ),
    )
    return _DispatchResult(message_id=message_id, log_extra={"reaction": emoji})
