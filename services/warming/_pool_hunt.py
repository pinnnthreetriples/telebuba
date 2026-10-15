"""One channel search: keyword hits, similar channels, the probes, the fits.

Shared by the pool search (``pool_fill``) and a warming account's own search
(``_self_search``). The caller hands in ``read`` — the one way this module reaches
Telegram — so each caller keeps its own fence, pauses and booking: a pool search reads
with an account outside warming, a self-search books every read on the visit's budget.

**A rate limit ends the search.** The first ``flood_wait`` read error is returned to
the caller untouched and nothing else is read: a search that kept going past it would
turn a soft limit into a hard one, which is exactly the retry storm the other warming
steps avoid by stopping the cycle. Any other read failure only skips that one read.

``max_reads`` bounds the whole search, probes included, so a caller with a budget never
spends past it.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.channel_tokens import dedup_key
from core.telegram_client import TelegramReadError
from schemas.telegram_action_results import LinkedDiscussionGroupResult
from schemas.telegram_actions import GetLinkedDiscussionGroup
from schemas.telegram_actions_activity import GetLastPostAt, LastPostResult
from schemas.telegram_actions_discovery import (
    GetSimilarChannels,
    SearchChannels,
    TelegramChannelMatch,
    TelegramChannelMatches,
)
from schemas.warming import WarmingChannel
from services.warming._pool_filters import is_alive, is_candidate, size_ok, text_ok

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from pydantic import BaseModel

    from schemas._warming_channels import WarmingChannelSource
    from schemas.telegram_actions import TelegramReadAction

    Reader = Callable[[TelegramReadAction], Awaitable[BaseModel]]


class _OutOfReadsError(Exception):
    """The search spent every read it was given."""


@dataclass(frozen=True, slots=True)
class HuntPlan:
    """What to look for and how much one search may read."""

    language: str
    country: str | None
    topics: tuple[str, ...]
    source: WarmingChannelSource
    max_probes: int
    max_reads: int
    similar_seeds: int = 2
    found_by: str | None = None


@dataclass(slots=True)
class HuntOutcome:
    """What a search saw: hits, probes, the fits, and the limit that stopped it if any."""

    found: int = 0
    checked: int = 0
    reads: int = 0
    failures: int = 0
    fits: list[WarmingChannel] = field(default_factory=list)
    # Hits the pool already knows (the caller decides which of them it may walk).
    known_hits: list[str] = field(default_factory=list)
    flood: TelegramReadError | None = None


class _Hunt:
    def __init__(self, read: Reader, plan: HuntPlan, known: set[str]) -> None:
        self._read = read
        self._plan = plan
        self._known = known
        self.hits: dict[str, TelegramChannelMatch] = {}
        self.outcome = HuntOutcome()

    async def _ask(self, action: TelegramReadAction) -> BaseModel | None:
        """One read on the budget; ``None`` when it failed for any reason but a limit."""
        if self.outcome.reads >= self._plan.max_reads:
            raise _OutOfReadsError
        self.outcome.reads += 1
        try:
            return await self._read(action)
        except TelegramReadError as exc:
            if exc.kind == "flood_wait":
                self.outcome.flood = exc
                raise _OutOfReadsError from exc
            self.outcome.failures += 1
            return None

    async def _matches(
        self, action: SearchChannels | GetSimilarChannels
    ) -> list[TelegramChannelMatch]:
        result = await self._ask(action)
        return result.items if isinstance(result, TelegramChannelMatches) else []

    def _keep(self, matches: list[TelegramChannelMatch]) -> None:
        for match in matches:
            if match.username:
                self.hits.setdefault(dedup_key(match.username), match)

    async def collect(self) -> None:
        for topic in self._plan.topics:
            self._keep(await self._matches(SearchChannels(query=topic, kind="all")))
        seeds = sorted(self.hits.values(), key=lambda m: m.participants_count or 0, reverse=True)
        for seed in seeds[: self._plan.similar_seeds]:
            self._keep(await self._matches(GetSimilarChannels(seed=f"@{seed.username}")))

    async def probe(self, match: TelegramChannelMatch, now: datetime) -> WarmingChannel | None:
        """The channel as the pool would store it, when its full info and last post fit.

        Search hits rarely carry a subscriber count, so ``getFullChannel`` answers it,
        with the about text the words and the language are checked against too.
        """
        handle = f"@{match.username}"
        full = await self._ask(GetLinkedDiscussionGroup(channel=handle))
        if not isinstance(full, LinkedDiscussionGroupResult):
            return None
        size = full.participants_count
        size = size if size is not None else match.participants_count
        text = " ".join(part for part in (match.title, full.about or "") if part)
        if not size_ok(size) or not text_ok(text, self._plan.language):
            return None
        last = await self._ask(GetLastPostAt(channel=handle))
        if not isinstance(last, LastPostResult) or not is_alive(last.last_post_at, now):
            return None
        return WarmingChannel(
            channel=dedup_key(match.username or ""),
            label=match.title or None,
            created_at=now.isoformat(),
            language=self._plan.language,
            country=self._plan.country,
            kind=match.kind,
            subscribers=size,
            last_post_at=last.last_post_at,
            source=self._plan.source,
            found_by=self._plan.found_by,
        )

    async def run(self) -> None:
        await self.collect()
        language = self._plan.language
        fitting = [m for m in self.hits.values() if is_candidate(m, language, self._known)]
        now = datetime.now(UTC)
        for match in fitting[: self._plan.max_probes]:
            self.outcome.checked += 1
            channel = await self.probe(match, now)
            if channel is not None:
                self.outcome.fits.append(channel)


async def hunt_channels(read: Reader, plan: HuntPlan, known: set[str]) -> HuntOutcome:
    """Search, probe and filter; the channels that fit, never more reads than planned."""
    hunt = _Hunt(read, plan, known)
    with suppress(_OutOfReadsError):
        await hunt.run()
    outcome = hunt.outcome
    outcome.found = len(hunt.hits)
    outcome.known_hits = [key for key in hunt.hits if key in known]
    return outcome
