"""Discovery stage 1 — what every wave shares: the budget, the wave result, the pacing.

Split from ``_discovery_waves`` (file-size cap). Nothing here talks to Telegram.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from services.neurocomment import _seams
from services.neurocomment._discovery_providers import SourceOutcome

if TYPE_CHECKING:
    from schemas.neurocomment_discovery import DiscoverySource
    from services.neurocomment._discovery_pool import AccountPool

# Short locale-neutral reason for a wave the run's read budget stopped. Deliberately not
# a run-level error — see ``_discovery_search._merge``.
READ_BUDGET = "read_budget"

# Native hits win a cross-source tie: their handles come straight from Telegram in
# canonical case, which is what adopt writes into the campaign verbatim. This governs
# the DEDUP order only — see ``_discovery_search._merge`` for why it must not govern
# truncation as well.
# Annotated because the keys are also iterated as the report's source list, where a
# widened ``str`` would not satisfy ``DiscoverySourceReport``.
# Also the board's row order — which the run's wave order no longer follows, since the
# operator's seed is read ahead of the wider post wave.
SOURCE_PRIORITY: dict[DiscoverySource, int] = {
    "telegram_search": 0,
    "telegram_posts": 1,
    "telegram_similar": 2,
    "telegram_recommended": 3,
}


class Wave(NamedTuple):
    """One wave's outcomes, and whether the run must stop reading after it."""

    outcomes: list[SourceOutcome]
    flooded: bool = False
    # A non-flood stop: the last account answered nothing often enough that the rest of
    # the run would only prove it again. Kept apart from ``flooded`` because only a flood
    # writes a cooldown and stops the run replacing its stored candidates.
    aborted: bool = False
    # A limit this run did not cause: the last account was already cooling when a read
    # was about to be spent. Its own field because it is neither of the two above —
    # nothing failed and nothing was written — but it must stop the run exactly like a
    # flood.
    cooled: bool = False

    @property
    def stopped(self) -> bool:
        return self.flooded or self.aborted or self.cooled


def stopped(outcomes: list[SourceOutcome], pool: AccountPool) -> Wave:
    """A wave the pool ended: its last account left, and ``dropped_reason`` says how."""
    reason = pool.dropped_reason
    return Wave(
        outcomes,
        flooded=reason == "flooded",
        aborted=reason == "aborted",
        cooled=reason == "cooling",
    )


class Budget:
    """Telegram reads the run has left, shared by every wave.

    One counter, not a cap per wave: what has to stay bounded is the traffic this run
    emits per account, and spending it in wave order is what puts the cheap keyword
    sweep first in line for it.

    ``hold`` fences reads off for a wave that has not run yet. Wave order alone left the
    LAST wave — Telegram's recommendations, the only source that reaches channels the
    account is nowhere near — living on whatever the post pages had not eaten, which on a
    full keyword list was nothing at all.
    """

    def __init__(self, total: int) -> None:
        self.left = total
        self.held = 0

    def hold(self, count: int) -> None:
        """Keep ``count`` of the remaining reads back for a later wave."""
        self.held = count

    def take(self) -> bool:
        """Claim one read. ``False`` means the run is out and the wave must stop."""
        if self.left - self.held <= 0:
            return False
        self.left -= 1
        return True


def skipped(
    source: DiscoverySource,
    reason: str | None = None,
    *,
    truncated: bool = False,
) -> SourceOutcome:
    """A source that was not asked (again) — never a silent absence from the report."""
    return SourceOutcome(source=source, state="skipped", error=reason, truncated=truncated)


async def pace() -> None:
    neuro = settings.neurocomment
    await _seams.sleep(
        _seams.rng.uniform(
            neuro.discovery_qualify_delay_min_seconds,
            neuro.discovery_qualify_delay_max_seconds,
        ),
    )


def unreached(outcomes: list[SourceOutcome]) -> list[SourceOutcome]:
    """Name every source this run never got to, so none is silently absent from the strip.

    A flood, or a sweep with no hit to seed the recommendation wave from, used to drop a
    whole row off the board — leaving the operator to guess whether that source had
    answered with nothing or had never been asked. No reason code: a FloodWait already
    rides the run's error and a second one would only compete with it.
    """
    reached = {outcome.source for outcome in outcomes}
    return [skipped(source) for source in SOURCE_PRIORITY if source not in reached]
