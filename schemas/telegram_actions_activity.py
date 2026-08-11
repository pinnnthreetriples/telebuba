"""Is this channel still alive? — the one read behind the inactive-channel rule.

Its own module rather than a member of ``telegram_actions_channels``: that cluster is
about a channel the ACCOUNT OWNS and keys on ``channel_id``, while this asks about a
channel the campaign merely watches and keys on the operator's handle string, which is
what neurocomment stores. ``telegram_actions`` itself is at the file-size cap.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class GetLastPostAt(BaseModel):
    """Read-only: when did this channel last publish anything?

    Deliberately not answerable from our own records. The listener only sees posts while
    the process is up, and this app restarts every day or two — so "we have seen nothing
    for a week" is equally consistent with a dead channel and with a week of downtime.
    Asking Telegram costs one read and is immune to both.
    """

    action_type: Literal["get_last_post_at"] = "get_last_post_at"
    channel: str = Field(min_length=1)


class LastPostResult(BaseModel):
    """ISO-8601 UTC of the newest message; ``None`` when the channel has never posted.

    ``None`` is NOT "we could not tell" — a read that fails raises instead, so the caller
    can hold its verdict rather than read a failure as silence.
    """

    last_post_at: str | None = None
