"""Reaction-emoji selection helpers for the warming react action.

Split out of :mod:`core.telegram_client._actions` to keep that module under the
file-size cap. ``_dispatch_react_to_post`` (which stays in ``_actions``) calls
these to read a channel's allowed reaction set and pick a landable emoji.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import ChatReactionsNone, ChatReactionsSome, ReactionEmoji

from core.config import settings

if TYPE_CHECKING:
    from telethon import TelegramClient

# SystemRandom: non-cryptographic selection, but avoids the module-level
# `random.*` calls that ruff S311 flags. Behaviour is identical for our needs.
_rng = random.SystemRandom()


async def _channel_reaction_whitelist(client: TelegramClient, channel: str) -> set[str] | None:
    """Emoticons the channel permits as reactions.

    ``None`` means "don't filter" — the channel allows all emoji (or the
    availability couldn't be read, in which case we fall back to the caller's
    default set rather than regress). An empty set means reactions are off or the
    channel only permits emoji we don't use, so the caller should skip entirely.
    """
    try:
        full = await client(GetFullChannelRequest(channel=channel))  # ty: ignore[invalid-argument-type]
    except Exception:  # noqa: BLE001 - availability is best-effort; don't fail the react over it.
        return None
    available = getattr(getattr(full, "full_chat", None), "available_reactions", None)
    if isinstance(available, ChatReactionsNone):
        return set()
    if isinstance(available, ChatReactionsSome):
        return {r.emoticon for r in available.reactions if isinstance(r, ReactionEmoji)}
    # ChatReactionsAll / unknown → any emoji is accepted, so don't narrow.
    return None


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
