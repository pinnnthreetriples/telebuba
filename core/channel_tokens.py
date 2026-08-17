"""Pure Telegram channel-token parsing — no I/O, no config reads.

Shared by warming (free-form paste box) and neurocomment discovery (search
results). The ``max_length`` bound is an explicit argument rather than a
``settings`` read so this module stays schema-neutral: warming allows the wider
``settings.warming.max_channel_length``, discovery clamps to Telegram's own
32-character username ceiling.
"""

from __future__ import annotations

import re

_INVITE_HASH_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# Allowed token format for a Telegram public channel/group identifier.
# Joinchat/invite links are intercepted earlier by extract_invite_hash.
_CHANNEL_TOKEN_RE = re.compile(r"^@?[A-Za-z0-9_]{3,32}$")

_LINK_PREFIXES = ("https://t.me/", "http://t.me/", "t.me/", "telegram.me/")


def extract_invite_hash(channel: str) -> str | None:
    """Extract the hash from a private invite link (``+HASH`` or ``joinchat/HASH``).

    Bare hashes without prefixes are intentionally not supported to avoid
    collisions with regular usernames.
    """
    cleaned = channel.strip().strip("<>").rstrip("/")
    cleaned = cleaned.split("?", 1)[0]
    for prefix in _LINK_PREFIXES:
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    if cleaned.startswith("+"):
        invite = cleaned[1:]
        return invite if _INVITE_HASH_RE.match(invite) else None
    if cleaned.lower().startswith("joinchat/"):
        invite = cleaned[9:]
        return invite if _INVITE_HASH_RE.match(invite) else None
    return None


def normalize_channel(token: str, *, max_length: int) -> str | None:  # noqa: PLR0911
    """Reduce any accepted form to a bare username or a ``+HASH`` invite key."""
    invite_hash = extract_invite_hash(token)
    if invite_hash:
        return f"+{invite_hash}"

    cleaned = token.strip().strip("<>").rstrip("/")
    if not cleaned:
        return None

    # Strip query parameters (like ?single)
    cleaned = cleaned.split("?")[0]

    lowered = cleaned.lower()
    for prefix in _LINK_PREFIXES:
        if lowered.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    else:
        # Reject bare tokens that contain a slash (e.g. channel/123)
        if "/" in cleaned:
            return None

    cleaned = cleaned.lstrip("@")
    if not cleaned:
        return None
    # Reject private chat links (e.g. t.me/c/12345/1)
    if cleaned.lower().startswith("c/"):
        return None

    # If it was a valid public post link (e.g. t.me/mychannel/123), extract the channel
    if "/" in cleaned:
        cleaned = cleaned.split("/")[0]
    if len(cleaned) > max_length:
        return None
    return cleaned if _CHANNEL_TOKEN_RE.match(cleaned) else None


def parse_message_link(link: str) -> tuple[str, int] | None:
    """Split a ``t.me`` message link into ``(peer_reference, message_id)``.

    Two shapes exist and both are accepted: ``t.me/<username>/<id>`` for a public
    chat, and ``t.me/c/<internal>/<id>`` for a private one. The private form's
    ``<internal>`` is the chat's RAW POSITIVE id — the same unmarked convention
    ``schemas.telegram_actions_chat`` pins — so it is handed back as a decimal
    string and the gateway feeds an all-digit reference to Telethon as an int.

    A thread link (``t.me/c/<internal>/<topic>/<id>``) names its message LAST, so
    the final segment is the id and the first is the peer. ``None`` for anything
    else, including a bare channel link with no message on it.
    """
    cleaned = link.strip().strip("<>").rstrip("/").split("?", 1)[0]
    lowered = cleaned.lower()
    for prefix in _LINK_PREFIXES:
        if lowered.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    else:
        return None
    parts = [part for part in cleaned.split("/") if part]
    private = bool(parts) and parts[0].lower() == "c"
    # A private link spends its first segment on the ``c`` marker, so it needs one
    # more than the public form before it names a message at all.
    minimum = 3 if private else 2
    if len(parts) < minimum or not parts[-1].isdigit():
        return None
    message_id = int(parts[-1])
    peer = parts[1] if private else parts[0].lstrip("@")
    if message_id <= 0 or not (peer.isdigit() if private else _CHANNEL_TOKEN_RE.match(peer)):
        return None
    return peer, message_id


def dedup_key(channel: str) -> str:
    """Case-folding key for dedup.

    Public usernames are case-insensitive and the leading ``@`` is decoration, so
    ``@News``, ``news`` and ``News`` are ONE channel — they resolve to a single
    Telegram peer id. Private-invite hashes ("+HASH") are case-sensitive, though:
    two genuinely different invites that differ only in letter case must not
    collapse to one and silently drop the second.
    """
    return channel if channel.startswith("+") else channel.lstrip("@").lower()


def channel_fold_sql(operand: str) -> str:
    """:func:`dedup_key` spelled as SQLite SQL over ``operand``.

    The neurocomment "one active campaign per channel" unique index (migration #39)
    is built over this expression, so it is the only fold spelling in the codebase:
    SQLite matches an expression index by comparing the expression itself, and any
    paraphrase (``lower(channel) = ?``) drops the per-post lookup from a SEARCH to a
    full SCAN.

    Comparisons fold BOTH sides through this helper rather than pre-folding the probe
    with :func:`dedup_key`. SQLite's ``lower()`` is ASCII-only where Python's
    ``str.lower()`` is full-Unicode, so a Python-folded probe disagrees with the index
    on any non-ASCII handle — a row the index has just accepted then reads as absent.
    """
    return (
        f"CASE WHEN substr({operand}, 1, 1) = '+' THEN {operand} "
        f"ELSE lower(ltrim({operand}, '@')) END"
    )


def parse_channels(raw: str, *, max_length: int) -> list[str]:
    """Split a free-form blob on whitespace/commas and dedup, preserving order."""
    seen: list[str] = []
    seen_keys: set[str] = set()
    for token in re.split(r"[\s,]+", raw.strip()):
        normalized = normalize_channel(token, max_length=max_length)
        if normalized is None:
            continue
        key = dedup_key(normalized)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        seen.append(normalized)
    return seen
