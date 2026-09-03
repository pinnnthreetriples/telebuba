"""Discovery stage 2 — the no-RPC facts: what a fresh cache row already answers.

Split from ``_discovery_qualify`` (file-size cap): everything a candidate can be
SETTLED from without a probe, so a repeat search over familiar channels costs no
Telegram read at all. ``is_fresh`` is re-exported from ``_discovery_qualify`` — the
adopt guard in ``discovery`` imports it from there and must apply the identical window.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.repositories.neurocomment import mark_discovery_qualified
from schemas.neurocomment_discovery import DiscoveryChannelVerdict
from services.neurocomment import _discovery_state
from services.neurocomment._discovery_categories import matches
from services.neurocomment._discovery_filters import (
    access_of,
    admit_at_qualification,
    detect_language,
    is_private_ref,
)

if TYPE_CHECKING:
    from schemas.neurocomment import LinkedDiscussionGroup
    from schemas.neurocomment_discovery import DiscoveryCandidateRow, DiscoverySearchRequest


class _Facts(NamedTuple):
    """The three derived facts the filters and the verdict share, whatever answered."""

    access: str | None
    language: str | None
    category_match: bool | None


def is_fresh(checked_at: str, now: datetime) -> bool:
    """Is this cached verdict still trustworthy? A zero TTL falls out as never.

    Module-public: the adopt guard in ``discovery`` must apply the SAME window as this
    probe loop, and reaching across a module boundary for a private name to do it said
    the opposite.
    """
    try:
        stamped = datetime.fromisoformat(checked_at)
    except ValueError:
        # Text column: a legacy or hand-edited row must re-probe, not raise.
        return False
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=UTC)
    ttl_hours = settings.neurocomment.discovery_linked_group_ttl_hours
    return stamped + timedelta(hours=ttl_hours) > now


def _facts(
    row: DiscoveryCandidateRow,
    request: DiscoverySearchRequest,
    *,
    about: str | None,
    join_request: bool | None,
) -> _Facts:
    """Access, language and category match — derived ONCE, for the verdict and the filters.

    The row's ``channel`` is a ref, not always a handle: a private ``id:`` row has no
    username, which is exactly what makes its access ``subscription``.
    """
    username = None if is_private_ref(row.channel) else row.channel
    category = request.category
    return _Facts(
        access=access_of(username, join_request),
        language=detect_language(f"{row.title} {about or ''}"),
        category_match=None if category == "any" else matches(row.title, about, category),
    )


def _is_group(kind: str) -> bool | None:
    """The row's stored kind as the verdict's tri-state: a legacy or blank kind is unknown.

    Not ``kind == "group"``: that read every unrecognised string as a confident "channel",
    and the comments filter then deleted the row on a fact nobody had measured.
    """
    return True if kind == "group" else False if kind == "channel" else None


def _cache_answers(group: LinkedDiscussionGroup, request: DiscoverySearchRequest) -> bool:
    """Does this fresh cache row carry every fact the active filters need?

    A pre-#61 row has ``NULL`` where the about text and the join gate should be — facts
    never learnt, not facts known to be blank — so a filter that reads them re-probes.
    """
    if (request.language != "any" or request.category != "any") and group.about is None:
        return False
    return not (request.access in {"open", "join_request"} and group.join_request is None)


async def _settled_without_probe(
    campaign_id: str,
    row: DiscoveryCandidateRow,
    fresh: dict[str, LinkedDiscussionGroup],
    request: DiscoverySearchRequest,
    rejected: list[str],
) -> bool:
    """Qualify the row from what is already known, if that is enough. No RPC either way."""
    if is_private_ref(row.channel):
        # Nothing can probe it, so the filters read the title alone and access is
        # ``subscription``. ``comments=False`` is an explicit rule, not a measurement: a
        # channel nobody can probe or comment in can never satisfy "has comments", so
        # ``comments=on`` refuses it rather than admitting on unknown.
        about, join_request, comments = None, None, False
    else:
        group = fresh.get(row.channel)
        if group is None or not _cache_answers(group, request):
            return False
        # Cache hit: no RPC, and deliberately no sleep — this is what makes a re-search
        # over familiar keywords finish in milliseconds. Every filter still applies.
        about, join_request, comments = group.about, group.join_request, group.comments_enabled
    facts = _facts(row, request, about=about, join_request=join_request)
    # Recorded on the cache path too: the board lifts access, language and the category
    # match off the verdict, so a row settled without a probe showed all three as unknown
    # — the very facts the filters had just read. The rights flags stay ``None``: nothing
    # measured them this run. ``is_group`` is the row's own kind.
    _discovery_state.record_verdict(
        campaign_id,
        row.channel,
        DiscoveryChannelVerdict(is_group=_is_group(row.kind), **facts._asdict()),
    )
    reason = _admit(row, facts, comments_enabled=comments, request=request)
    await _settle(campaign_id, row.channel, reason, rejected)
    return True


def _admit(
    row: DiscoveryCandidateRow,
    facts: _Facts,
    *,
    comments_enabled: bool | None,
    request: DiscoverySearchRequest,
) -> str | None:
    # A group's comments verdict is structurally False (comments ARE its messages), so it
    # is handed over as unknown: the filter must not delete every group a ``kind=all``
    # search found the moment the operator asks for comments on. Only a row KNOWN to be
    # a channel hands the verdict over — an unknown kind is not a channel by default.
    return admit_at_qualification(
        comments_enabled=comments_enabled if _is_group(row.kind) is False else None,
        access=facts.access,
        language=facts.language,
        category_match=facts.category_match,
        request=request,
    )


async def _settle(
    campaign_id: str,
    channel: str,
    reason: str | None,
    rejected: list[str],
    *,
    subscribers: int | None = None,
) -> None:
    """Keep the row as qualified, or queue it for deletion when an operator filter refused it.

    Shared by both paths: the cache path never has a subscriber count to backfill, the
    probe path always does.
    """
    if reason is None:
        await mark_discovery_qualified(campaign_id, channel, subscribers=subscribers)
        return
    rejected.append(channel)
    _discovery_state.bump_filtered(campaign_id, reason)
