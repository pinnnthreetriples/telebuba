"""One run's working state: its sources, what they gave, and how each account reaches them.

All of it is in memory and lives as long as the run's task. Only the settled run — its
counters, its source statuses and the people it kept — reaches the database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.channel_tokens import normalize_channel, parse_message_link
from schemas.user_parser_run import UserParserSourceReport

if TYPE_CHECKING:
    from datetime import datetime

    from schemas.user_parser import UserParserRequest
    from schemas.user_parser_run import UserParserRun
    from services.user_parser._collect import Collector

# Telegram usernames are at most 32 characters; ``normalize_channel`` reads an invite
# (``+HASH``) before it measures, so invites are not cut by this.
_HANDLE_MAX = 32


@dataclass(slots=True)
class SourceState:
    """One line of the operator's source list, as the run works through it."""

    raw: str
    # A bare username or ``+HASH``; ``None`` when the line is no Telegram chat at all.
    token: str | None
    # A forum topic (``t.me/chat/123``), read in the messages mode only.
    topic: int | None
    report: UserParserSourceReport
    # Members walked, messages read, or posts looked at — whichever the mode counts.
    seen: int = 0
    # Comments read per post, comments mode.
    per_post: dict[int, int] = field(default_factory=dict)
    admins_queued: bool = False
    # What the source's pages ran into; the status is decided once its last page is in.
    flooded: bool = False
    failed: bool = False
    partial: bool = False

    @property
    def invite(self) -> bool:
        return self.token is not None and self.token.startswith("+")

    @property
    def closed(self) -> bool:
        """Nothing more will be read here: the list is hidden or the chat could not be joined."""
        return self.report.status in {"hidden", "join_failed"}


def source_state(raw: str, *, messages_mode: bool) -> SourceState:
    token = normalize_channel(raw, max_length=_HANDLE_MAX)
    topic = None
    if messages_mode and token is not None and not token.startswith("+"):
        link = parse_message_link(raw)
        topic = None if link is None else link[1]
    return SourceState(raw=raw, token=token, topic=topic, report=UserParserSourceReport(source=raw))


@dataclass(slots=True)
class RunContext:
    run_id: str
    request: UserParserRequest
    sources: list[SourceState]
    collector: Collector
    live: UserParserRun
    # Oldest message the messages mode reads.
    min_date: datetime | None = None
    # (account, source) -> the peer this account reads the source by.
    access: dict[tuple[str, int], str] = field(default_factory=dict)
    admin_ids: set[int] = field(default_factory=set)
