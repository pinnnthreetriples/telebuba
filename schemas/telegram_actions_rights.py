"""The write-rights read: why a chat refused our comment, and whose doing it was.

Its own module for the reason every other ``telegram_actions_*`` sibling exists — the
parent is at the file-size cap — and along the same seam: one read action plus the one
result type nothing else builds. ``schemas.telegram_actions`` imports both back, so
``from schemas.telegram_actions import CheckWriteRights`` resolves unchanged and the read
union there still references them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CheckWriteRights(BaseModel):
    """Read-only: WHY can this account not write in ``channel``'s discussion group?

    ``ChatWriteForbiddenError`` says only "not here". It cannot tell a chat closed to
    EVERYONE (comments off / read-only group — the account is innocent and the CHANNEL is
    the thing out of service) from a mute an admin put on THIS ONE account (nothing is
    wrong with the channel, and Telegram even carries an expiry). The two need opposite
    responses, so this asks for both records at once: the group's own
    ``default_banned_rights`` and our participant record's ``banned_rights``. Comments
    live in the linked discussion group, so the gateway resolves it exactly like
    ``CheckBannedInChannel`` — a pure read, nothing is sent.
    """

    action_type: Literal["check_write_rights"] = "check_write_rights"
    channel: str = Field(min_length=1)


class WriteRightsResult(BaseModel):
    """Gateway output for ``CheckWriteRights`` — WHOSE mute is stopping the write.

    ``scope``: ``everyone`` = the group's ``default_banned_rights`` revoke
    ``send_messages``, so the chat is read-only for all and this account is not at fault;
    ``self_only`` = our own participant record is restricted, so only this pair is muted;
    ``none`` = the rights permit writing, so the refusal came from something else;
    ``unknown`` = nothing could be read, which is never a verdict.

    ``muted_until`` is the ISO-8601 expiry Telegram carries on a ``self_only`` mute, and
    ``None`` whenever there is none: a permanent restriction encodes ``until_date`` as 0,
    which Telethon's date reader hands back as no date at all. Handed on raw — bounding an
    over-long or absent wait is the caller's rule, not the gateway's. Deliberately not
    read for ``everyone``: that answer costs the channel its link, not a wait.

    ``reason`` is the content-free label behind a non-verdict: ``no_linked_group``,
    ``not_member``, or a read failure collapsed the way ``TelegramReadError`` already
    collapses one (``RPC: <ClassName>``), so it stays machine-readable.
    """

    scope: Literal["everyone", "self_only", "none", "unknown"]
    muted_until: str | None = None
    reason: str | None = None
