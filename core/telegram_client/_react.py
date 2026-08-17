"""The warming react action: emoji selection plus the dispatch that places one.

Split out of :mod:`core.telegram_client._actions` to keep that module under the
file-size cap; ``_actions`` keeps the ``match`` arm and delegates the body here.
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ChatReactionsNone, ChatReactionsSome, ReactionEmoji

from core.config import settings
from core.telegram_client._action_results import _DispatchResult

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import ReactToPost
    from schemas.telegram_actions_chat import ReactToMessage

# SystemRandom: non-cryptographic selection, but avoids the module-level
# `random.*` calls that ruff S311 flags. Behaviour is identical for our needs.
_rng = random.SystemRandom()

# A channel's allowed-reaction set changes rarely, but the react action re-read it
# on every reaction. Cache it per channel for an hour. Single event loop / one
# uvicorn worker (CLAUDE.md) → no lock needed. Failures are not cached so a
# transient error re-probes next time rather than sticking a bad "None".
_WHITELIST_TTL_SECONDS = 3600.0
_whitelist_cache: dict[str, tuple[float, set[str] | None]] = {}
# A successful channel-management write can land while a warming reaction is
# awaiting Telegram.  Clearing the dict alone is insufficient: an older
# whitelist read could then finish and put its pre-write value back.  The
# generation prevents that stale repopulation and lets the dispatcher abandon a
# reaction whose permission decision predates the write.
_whitelist_generation = 0


def invalidate_reaction_whitelist_cache() -> None:
    """Retire every cached channel-reaction decision.

    Channel management addresses an owned channel by its numeric id, while
    warming stores public usernames.  Without another Telegram lookup there is
    no safe one-key mapping between those forms, so the rare management write
    invalidates the small process-local cache as a whole.
    """
    global _whitelist_generation  # noqa: PLW0603 - the generation is process-local state.
    _whitelist_generation += 1
    _whitelist_cache.clear()


async def _channel_reaction_whitelist(
    client: TelegramClient,
    channel: str | int,
) -> set[str] | None:
    """Emoticons the channel permits as reactions.

    ``None`` means "don't filter" — the channel allows all emoji (or the
    availability couldn't be read, in which case we fall back to the caller's
    default set rather than regress). An empty set means reactions are off or the
    channel only permits emoji we don't use, so the caller should skip entirely.

    ``channel`` is a username for warming and a raw positive chat id for the
    chat-scoped reactor; the cache key is its string form either way, while the RPC
    gets the value itself — Telethon reads an int out of the session entity cache
    and would send the same digits down the username resolver as a string.
    """
    generation = _whitelist_generation
    now = time.monotonic()
    key = str(channel)
    cached = _whitelist_cache.get(key)
    if cached is not None and now - cached[0] < _WHITELIST_TTL_SECONDS:
        return cached[1]
    try:
        full = await client(GetFullChannelRequest(channel=channel))  # ty: ignore[invalid-argument-type]
    except errors.FloodError:
        # The one failure that must NOT degrade to "don't filter": swallowing it sent
        # the reaction itself into an active flood, spending the very budget Telegram
        # had just refused. Re-raised so ``execute``'s rate-limit ladder classifies it
        # and the caller waits.
        raise
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
    # A settings mutation may have completed during GetFullChannelRequest.  Its
    # invalidation wins: never resurrect the older availability for another TTL.
    if generation == _whitelist_generation:
        _whitelist_cache[key] = (now, result)
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
    whitelist_generation = _whitelist_generation
    allowed = await _channel_reaction_whitelist(client, action.channel)
    emoji = _pick_reaction(action.reactions, allowed)
    if emoji is None:
        return _DispatchResult(log_extra={"reaction_skip": "no_emoji"})
    message_id = _rng.choice(candidates)
    peer = await client.get_input_entity(action.channel)
    # The operator may have disabled/edited reactions while peer resolution was
    # in flight.  Do not dispatch from a permission decision that the successful
    # write has already invalidated.
    if whitelist_generation != _whitelist_generation:
        return _DispatchResult(log_extra={"reaction_skip": "reaction_settings_changed"})
    await client(
        SendReactionRequest(
            peer=peer,
            msg_id=message_id,
            reaction=[ReactionEmoji(emoticon=emoji)],
        ),
    )
    return _DispatchResult(message_id=message_id, log_extra={"reaction": emoji})


async def dispatch_react_to_message(
    client: TelegramClient,
    action: ReactToMessage,
) -> _DispatchResult:
    """Place ONE named emoji on ONE named message — or skip, never fail.

    Shares the per-chat allowed-emoji cache with :func:`dispatch_react_to_post`,
    because a blind send is what trips ``ReactionInvalidError`` on chats that
    restrict reactions. What differs is the remedy: there the emoji is ours to pick,
    so a restricted chat gets a substitute; here the operator chose it, and a
    substitute would publish a reaction they did not write. So a chat that does not
    permit this emoji — or permits none at all (``ChatReactionsNone``) — SKIPS the
    step. A reaction the chat forbids is not a failure of the campaign, and marking
    it one would spend a reserve account on a chat setting.

    ``ReactionInvalidError`` / ``ReactionsTooManyError` are the same verdict arriving
    late: the whitelist can be stale, absent (unreadable) or simply not cover custom
    limits, and a non-Premium account may hold exactly one reaction per message.
    """
    whitelist_generation = _whitelist_generation
    allowed = await _channel_reaction_whitelist(client, action.chat_id)
    emoji = _bare_emoji(action.emoji)
    if allowed is not None and emoji not in {_bare_emoji(e) for e in allowed}:
        return _DispatchResult(log_extra={"reaction_skip": "not_allowed"})
    peer = await client.get_input_entity(action.chat_id)
    if whitelist_generation != _whitelist_generation:
        return _DispatchResult(log_extra={"reaction_skip": "reaction_settings_changed"})
    try:
        await client(
            SendReactionRequest(
                peer=peer,
                msg_id=action.message_id,
                reaction=[ReactionEmoji(emoticon=emoji)],
            ),
        )
    except (errors.ReactionInvalidError, errors.ReactionsTooManyError):
        return _DispatchResult(log_extra={"reaction_skip": "not_allowed"})
    return _DispatchResult(message_id=action.message_id, log_extra={"reaction": emoji})
