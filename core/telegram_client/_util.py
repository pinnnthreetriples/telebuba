"""Small shared helpers for the Telegram gateway submodules."""

from __future__ import annotations

# Invite-link parsing lives in core.channel_tokens (pure, shared with warming and
# neurocomment discovery); re-exported here so gateway call sites stay unchanged.
from core.channel_tokens import extract_invite_hash

__all__ = ["event_name", "extract_invite_hash", "optional_str"]


def event_name(domain: str | None, name: str) -> str:
    """Stamp a gateway log-event name with the domain that called ``execute``.

    There is ONE ``logs`` table and the per-domain feeds separate only by
    ``event LIKE 'prefix%'``, so a bare ``telegram_*`` name is invisible in its
    caller's own feed and leaks into every other one. ``domain`` is bound at the
    service seam (``services.<domain>._seams.execute``); ``None`` — accounts,
    profile and channel actions, which have no per-domain feed — keeps the bare
    name.
    """
    return f"{domain}_{name}" if domain else name


def optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
