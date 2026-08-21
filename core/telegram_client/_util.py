"""Small shared helpers for the Telegram gateway submodules."""

from __future__ import annotations

# Invite-link parsing lives in core.channel_tokens (pure, shared with warming and
# neurocomment discovery); re-exported here so gateway call sites stay unchanged.
from core.channel_tokens import extract_invite_hash

__all__ = [
    "event_name",
    "extract_invite_hash",
    "id_strings",
    "optional_str",
    "sent_message_id",
]


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


def sent_message_id(message: object) -> int | None:
    """Id of a message we just sent, ``None`` when the reply carried none.

    Shared rather than inlined at its three ``_actions`` call sites because the
    ``or`` it contains is a decision point, and ``_dispatch_action`` already sat at
    the cyclomatic ceiling ``tools/radon_gate.py`` enforces.
    """
    return int(getattr(message, "id", 0)) or None


def id_strings(values: list[int] | None) -> list[str] | None:
    """Telegram int64 ids as decimal strings for the JSON boundary; ``None`` stays ``None``.

    Past JavaScript's 2^53 safe-integer window, so every id crossing into a
    response is a string (see ``ActionResult.channel_id``). Extracted from
    ``execute``, which the aislop function-length gate holds to 80 lines.
    """
    return None if values is None else [str(value) for value in values]
