"""The parser's filters — pure functions, nothing here talks to Telegram or the database.

Two levels, because message text is never stored. A MESSAGE either counts towards its
author or not (:func:`message_counts`: replies, forwards, keywords, a comment's length) and
is decided the moment the page is read. A PERSON is then kept or dropped once the run is
over (:func:`rejection`), in the order the modal's hints describe them.

What "kept" means per toggle mirrors the prototype's ``applyFilters``; ``hidden`` last-seen
passes only under "any", because Telegram puts such a user in no bucket.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable

    from schemas.telegram_actions_user_parser import ChatHistoryMessage, LastSeenBucket
    from schemas.user_parser import LastSeenWant, UserParserRequest
    from services.user_parser._collect import UserAggregate

Rejection = Literal[
    "bot",
    "deleted",
    "scam",
    "own",
    "admin",
    "collected",
    "blacklist",
    "stop_word",
    "no_username",
    "no_photo",
    "not_premium",
    "no_stories",
    "last_seen",
    "min_messages",
    "min_sources",
]

_SEEN_RANK: dict[LastSeenBucket, int] = {
    "online": 0,
    "recently": 1,
    "week": 2,
    "month": 3,
    "long": 4,
    "hidden": 5,
}
_WANT_RANK: dict[LastSeenWant, int] = {"any": 5, "recently": 1, "week": 2, "month": 3}
_LINK_PREFIXES = ("https://t.me/", "http://t.me/", "t.me/")


def message_counts(message: ChatHistoryMessage, request: UserParserRequest) -> bool:
    """Does this message count towards its author?

    Replies and forwards are choices of the messages mode only: under a channel post every
    comment is a reply. Keywords match case-insensitively anywhere in the text; the
    shortest comment is the comments mode's.
    """
    toggles = request.toggles
    if request.mode == "messages":
        if message.is_reply and not toggles.include_replies:
            return False
        if message.is_forward and not toggles.include_forwards:
            return False
    if request.mode == "comments" and len(message.text.strip()) < request.limits.min_length:
        return False
    if request.keywords:
        text = message.text.casefold()
        return any(word.casefold() in text for word in request.keywords)
    return True


def _blacklist_key(entry: str) -> str:
    cleaned = entry.strip().casefold()
    for prefix in _LINK_PREFIXES:
        cleaned = cleaned.removeprefix(prefix)
    return cleaned.lstrip("@")


@dataclass(frozen=True, slots=True)
class FilterContext:
    """Everything a person is judged against, gathered once per run."""

    request: UserParserRequest
    own_ids: frozenset[int] = frozenset()
    admin_ids: frozenset[int] = frozenset()
    collected_ids: frozenset[int] = frozenset()
    blacklist: frozenset[str] = field(init=False)
    stop_words: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        listed = frozenset(_blacklist_key(entry) for entry in self.request.blacklist)
        object.__setattr__(self, "blacklist", listed - {""})
        words = tuple(w.strip().casefold() for w in self.request.stop_words if w.strip())
        object.__setattr__(self, "stop_words", words)


def _skip(agg: UserAggregate, ctx: FilterContext) -> Rejection | None:
    user, toggles = agg.user, ctx.request.toggles
    if toggles.skip_bots and user.is_bot:
        return "bot"
    if toggles.skip_deleted and user.is_deleted:
        return "deleted"
    if toggles.skip_scam and (user.is_scam or user.is_fake):
        return "scam"
    return None


def _excluded(agg: UserAggregate, ctx: FilterContext) -> Rejection | None:
    user, toggles = agg.user, ctx.request.toggles
    if toggles.exclude_own and user.user_id in ctx.own_ids:
        return "own"
    if toggles.exclude_admins and user.user_id in ctx.admin_ids:
        return "admin"
    if toggles.exclude_collected and user.user_id in ctx.collected_ids:
        return "collected"
    return None


def _listed(agg: UserAggregate, ctx: FilterContext) -> Rejection | None:
    user = agg.user
    handle = (user.username or "").casefold()
    if (handle and handle in ctx.blacklist) or str(user.user_id) in ctx.blacklist:
        return "blacklist"
    haystack = f"{user.first_name} {user.last_name} {handle}".casefold()
    if any(word in haystack for word in ctx.stop_words):
        return "stop_word"
    return None


def _profile(agg: UserAggregate, ctx: FilterContext) -> Rejection | None:
    user, toggles = agg.user, ctx.request.toggles
    if toggles.with_username and not user.username:
        return "no_username"
    if toggles.with_photo and not user.has_photo:
        return "no_photo"
    if toggles.premium_only and not user.is_premium:
        return "not_premium"
    if toggles.with_stories and not user.has_stories:
        return "no_stories"
    if _SEEN_RANK[user.last_seen] > _WANT_RANK[ctx.request.last_seen]:
        return "last_seen"
    return None


def _activity(agg: UserAggregate, ctx: FilterContext) -> Rejection | None:
    request = ctx.request
    if request.mode != "members" and agg.message_count < request.min_messages:
        return "min_messages"
    if len(agg.sources) < request.min_sources:
        return "min_sources"
    return None


def rejection(agg: UserAggregate, ctx: FilterContext) -> Rejection | None:
    """Why this person is dropped — the first filter they fail — or ``None`` to keep them."""
    return (
        _skip(agg, ctx)
        or _excluded(agg, ctx)
        or _listed(agg, ctx)
        or _profile(agg, ctx)
        or _activity(agg, ctx)
    )


def apply_filters(
    people: Iterable[UserAggregate], ctx: FilterContext
) -> tuple[list[UserAggregate], dict[str, int]]:
    """The people kept, and how many each filter dropped."""
    kept: list[UserAggregate] = []
    dropped: dict[str, int] = {}
    for agg in people:
        reason = rejection(agg, ctx)
        if reason is None:
            kept.append(agg)
        else:
            dropped[reason] = dropped.get(reason, 0) + 1
    return kept, dropped
