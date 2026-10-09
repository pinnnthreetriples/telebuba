"""Pure parsing and rendering: chat links, post links, ``{a|b}`` variants and variables.

No I/O. The chat key is the identity a chat keeps across the run: a lower-cased
username, a case-sensitive ``+HASH`` invite, ``folder:<slug>:<id>`` for a chat reached
through a shared folder, ``id:<id>`` for a group an account is already in.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, NamedTuple

from core.channel_tokens import dedup_key, normalize_channel, parse_message_link

if TYPE_CHECKING:
    import random

    from schemas.chat_broadcast_board import ChatBroadcastTargetKind

_LINK_PREFIXES = ("https://t.me/", "http://t.me/", "t.me/", "telegram.me/")
_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Innermost ``{…|…}`` group: resolved repeatedly, so nested variants work too.
_SPINTAX_RE = re.compile(r"\{([^{}]*\|[^{}]*)\}")
_VARIABLES = ("{group_title}", "{group_username}")


class ParsedTarget(NamedTuple):
    key: str
    kind: ChatBroadcastTargetKind
    # What the join is made with: a bare username, ``+HASH``, or the folder slug.
    token: str


def _strip_link(value: str) -> str:
    cleaned = value.strip().strip("<>").rstrip("/").split("?", 1)[0]
    lowered = cleaned.lower()
    for prefix in _LINK_PREFIXES:
        if lowered.startswith(prefix):
            return cleaned[len(prefix) :]
    return cleaned


def folder_slug(value: str) -> str | None:
    """The slug of a ``t.me/addlist/<slug>`` folder link, or ``None``."""
    stripped = _strip_link(value)
    if not stripped.lower().startswith("addlist/"):
        return None
    slug = stripped[len("addlist/") :]
    return slug if _SLUG_RE.fullmatch(slug) else None


def parse_target(value: str, *, max_length: int) -> ParsedTarget | None:
    """Classify one pasted link: a public chat, a private invite, or a folder."""
    slug = folder_slug(value)
    if slug is not None:
        return ParsedTarget(key=f"addlist:{slug}", kind="folder", token=slug)
    if _strip_link(value).lower().startswith("addlist"):
        return None
    token = normalize_channel(value, max_length=max_length)
    if token is None:
        return None
    kind: ChatBroadcastTargetKind = "invite" if token.startswith("+") else "public"
    return ParsedTarget(key=dedup_key(token), kind=kind, token=token)


def split_targets(values: list[str]) -> list[str]:
    """One link per item even when the operator pasted several per line."""
    return [part for value in values for part in re.split(r"[\s,;]+", value) if part]


def folder_chat_key(slug: str, peer_id: int) -> str:
    return f"folder:{slug}:{peer_id}"


def own_chat_key(peer_id: int) -> str:
    return f"id:{peer_id}"


def parse_post(link: str) -> tuple[str, int] | None:
    """``(channel reference, message id)`` of a post to forward, or ``None``."""
    return parse_message_link(link)


def render(
    text: str,
    *,
    title: str | None,
    username: str | None,
    rng: random.Random,
) -> str:
    """Pick one variant out of every ``{a|b}`` group, then fill the chat variables."""
    rendered = text
    while (match := _SPINTAX_RE.search(rendered)) is not None:
        choice = rng.choice(match.group(1).split("|"))
        rendered = rendered[: match.start()] + choice + rendered[match.end() :]
    return rendered.replace(_VARIABLES[0], title or "").replace(
        _VARIABLES[1], f"@{username}" if username else ""
    )


def has_variants(text: str) -> bool:
    return _SPINTAX_RE.search(text) is not None
