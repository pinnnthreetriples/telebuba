"""Small shared helpers for the Telegram gateway submodules."""

from __future__ import annotations

import re

_INVITE_HASH_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def extract_invite_hash(channel: str) -> str | None:
    """Extract the hash from a private invite link (``+HASH`` or ``joinchat/HASH``).

    Bare hashes without prefixes are intentionally not supported to avoid
    collisions with regular usernames.
    """
    cleaned = channel.strip().strip("<>").rstrip("/")
    cleaned = cleaned.split("?", 1)[0]
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "telegram.me/"):
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
