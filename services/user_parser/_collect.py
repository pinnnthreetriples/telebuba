"""What a run has found so far: one aggregate per person, deduplicated by ``user_id``.

A person SEEN in a source (a member, the author of any message) counts towards the raw
total and that source's count. Only a message that COUNTS (``_filters.message_counts``)
raises ``message_count``, moves the first/last dates and puts the source on the person's
list — so "in at least N sources" and "at least N messages" both measure what the
operator's keywords and toggles let through. A member is counted by being there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from schemas.user_parser_run import UserParserUser

if TYPE_CHECKING:
    from datetime import datetime

    from schemas.telegram_actions_user_parser import ParsedTelegramUser


@dataclass(slots=True)
class UserAggregate:
    user: ParsedTelegramUser
    message_count: int = 0
    first_at: datetime | None = None
    last_at: datetime | None = None
    # Insertion-ordered set of source labels.
    sources: dict[str, None] = field(default_factory=dict)

    def record(self) -> UserParserUser:
        user = self.user
        return UserParserUser(
            **user.model_dump(),
            message_count=self.message_count,
            first_at=None if self.first_at is None else self.first_at.isoformat(),
            last_at=None if self.last_at is None else self.last_at.isoformat(),
            sources=list(self.sources),
        )


class Collector:
    """The people of one run, and which of them each source gave."""

    def __init__(self, sources: list[str]) -> None:
        self._labels = sources
        self.people: dict[int, UserAggregate] = {}
        self._per_source: list[set[int]] = [set() for _ in sources]

    @property
    def raw(self) -> int:
        return len(self.people)

    def source_count(self, index: int) -> int:
        return len(self._per_source[index])

    def see(self, user: ParsedTelegramUser, index: int) -> UserAggregate:
        """Register a person seen in a source; the newest profile snapshot wins."""
        agg = self.people.get(user.user_id)
        if agg is None:
            agg = UserAggregate(user=user)
            self.people[user.user_id] = agg
        else:
            agg.user = user
        self._per_source[index].add(user.user_id)
        return agg

    def member(self, user: ParsedTelegramUser, index: int) -> None:
        self.see(user, index).sources[self._labels[index]] = None

    def message(self, user: ParsedTelegramUser, index: int, at: datetime, *, counts: bool) -> None:
        agg = self.see(user, index)
        if not counts:
            return
        agg.message_count += 1
        agg.sources[self._labels[index]] = None
        if agg.first_at is None or at < agg.first_at:
            agg.first_at = at
        if agg.last_at is None or at > agg.last_at:
            agg.last_at = at
