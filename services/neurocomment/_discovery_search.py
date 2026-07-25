"""Discovery stage 1 — fan out to the enabled sources, merge, persist candidates.

Pacing note: the keyword RPCs are jittered exactly like the qualification pass.
Even a modest sweep is ~11 reads, and firing them as one burst is the freeze
vector the whole discovery design is built to avoid.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.channel_tokens import dedup_key, normalize_channel
from core.config import settings
from core.repositories.neurocomment import replace_discovery_candidates
from schemas.neurocomment_discovery import (
    CHANNEL_HANDLE_MAX_LENGTH,
    DiscoveryCandidateRow,
)
from services.neurocomment import _seams
from services.neurocomment._discovery_providers import (
    search_native,
    search_similar,
    search_telemetr,
)

if TYPE_CHECKING:
    from schemas.neurocomment_discovery import DiscoverySearchRequest
    from services.neurocomment._discovery_providers import RawCandidate, SourceOutcome

# Native hits win a cross-source tie: their handles come straight from Telegram in
# canonical case, which is what adopt writes into the campaign verbatim.
_SOURCE_PRIORITY = {"telegram_search": 0, "telegram_similar": 1, "telemetr": 2}


async def _pace() -> None:
    neuro = settings.neurocomment
    await _seams.sleep(
        _seams.rng.uniform(
            neuro.discovery_qualify_delay_min_seconds,
            neuro.discovery_qualify_delay_max_seconds,
        ),
    )


def _within_member_bounds(candidate: RawCandidate, request: DiscoverySearchRequest) -> bool:
    """Re-apply the subscriber filter to hits whose count we happen to know.

    Telemetr filters server-side; native search usually returns no count at all, and
    an unknown count must not be silently dropped — it gets filled in during
    qualification, and the operator can see it then.
    """
    if candidate.subscribers is None:
        return True
    if request.members_min is not None and candidate.subscribers < request.members_min:
        return False
    return not (request.members_max is not None and candidate.subscribers > request.members_max)


def _merge(
    outcomes: list[SourceOutcome],
    request: DiscoverySearchRequest,
) -> tuple[list[DiscoveryCandidateRow], str | None]:
    """Normalize, dedup and cap the union of every source's hits."""
    ranked: list[RawCandidate] = []
    for outcome in outcomes:
        ranked.extend(outcome.candidates)
    # Stable sort by source priority so the dedup below keeps the preferred spelling.
    ranked.sort(key=lambda candidate: _SOURCE_PRIORITY.get(candidate.source, 99))

    rows: list[DiscoveryCandidateRow] = []
    seen: set[str] = set()
    for candidate in ranked:
        handle = normalize_channel(candidate.username, max_length=CHANNEL_HANDLE_MAX_LENGTH)
        if handle is None or handle.startswith("+"):
            # Invite-only links have no public handle to search or comment under.
            continue
        key = dedup_key(handle)
        if key in seen:
            continue
        if not _within_member_bounds(candidate, request):
            continue
        seen.add(key)
        rows.append(
            DiscoveryCandidateRow(
                channel=handle,
                title=candidate.title,
                subscribers=candidate.subscribers,
                source=candidate.source,
            ),
        )
    # First error wins: the board shows one short reason, not a concatenation.
    error = next((outcome.error for outcome in outcomes if outcome.error), None)
    return rows[: settings.neurocomment.discovery_max_candidates], error


async def run_search(
    campaign_id: str,
    account_id: str,
    request: DiscoverySearchRequest,
) -> tuple[int, str | None]:
    """Collect candidates from every enabled source and persist the merged set.

    Returns the candidate count plus the first degraded-source reason (if any). A
    source that fails is recorded, never raised: the other source's results still
    have value to the operator.
    """
    outcomes: list[SourceOutcome] = []
    keywords = [keyword.strip() for keyword in request.keywords]

    for index, keyword in enumerate(keywords):
        if index:
            await _pace()
        outcomes.append(await search_native(account_id, keyword))
        if request.use_telemetr:
            # HTTP to a third party costs no Telegram flood budget, so it needs no pause.
            outcomes.append(await search_telemetr(keyword, request))

    if request.seed_channel:
        await _pace()
        outcomes.append(await search_similar(account_id, request.seed_channel))

    rows, error = _merge(outcomes, request)
    await replace_discovery_candidates(campaign_id, rows)
    return len(rows), error
