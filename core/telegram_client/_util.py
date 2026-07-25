"""Small shared helpers for the Telegram gateway submodules."""

from __future__ import annotations

# Invite-link parsing lives in core.channel_tokens (pure, shared with warming and
# neurocomment discovery); re-exported here so gateway call sites stay unchanged.
from core.channel_tokens import extract_invite_hash

__all__ = ["extract_invite_hash", "optional_str"]


def optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
