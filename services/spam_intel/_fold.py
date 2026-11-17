"""The fold: an account's evidence and the clock in, one verdict out (design section 5.4).

Pure and deterministic: no database, no clock, no randomness; the same evidence in any order
folds to the same verdict. Weights and half-lives come from ``_evidence_policy``.

1. A freeze that was not closed by ``freeze_cleared`` is ``frozen``, and nothing else (clean
   evidence of any kind, ``spambot_clean`` included) moves it.
2. Otherwise the unexpired limited items that no clean item supersedes decide ``limited``. A
   clean item SUPERSEDES a limited one when it is newer and its base weight is not lower.
   When a newer clean item exists that does not supersede (a delivered first message after a
   @SpamBot limit), the verdict stays ``limited`` on the safe side and is flagged
   ``contradiction`` + ``needs_probe``: the one evidence-driven reason to ask @SpamBot.
3. Else the strongest clean item decides ``clean``.
4. Else ``unknown``. A dated limit that has run out contributes nothing, so an account whose
   only news is such a limit reads ``unknown`` with reason ``limit_expired`` and ``needs_probe``:
   this is what ends a "stuck limited" state.

Confidence is the strength of the deciding item in three buckets. ``tos_restricted`` never
moves the verdict; it is reported as a flag with its reason.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from schemas.spam_evidence import Evidence, EvidenceKind, Verdict, VerdictReason
from services.spam_intel._evidence_policy import KIND_POLICY, SPAMBOT_KINDS, strength

if TYPE_CHECKING:
    from collections.abc import Iterable

    from core._config_spam_intel import SpamIntelSettings
    from schemas.spam_evidence import ConfidenceBucket

HIGH = 0.75
MEDIUM = 0.4
# A clean reading below this strength is flagged ``stale``.
STALE_BELOW = 0.5

type _Scored = tuple[Evidence, float]


def bucket(value: float) -> ConfidenceBucket:
    """High from 0.75, medium from 0.4, else low."""
    if value >= HIGH:
        return "high"
    return "medium" if value >= MEDIUM else "low"


def _strongest(pairs: list[_Scored]) -> _Scored:
    """The strongest item; ties break on recency, then names, so order never matters."""
    return max(pairs, key=lambda p: (p[1], p[0].observed_at, p[0].kind, p[0].source))


def _active(items: list[Evidence], now: datetime, direction: str) -> list[_Scored]:
    """Unexpired items of one direction that carry weight."""
    scored = [
        (item, strength(item, now))
        for item in items
        if KIND_POLICY[item.kind].direction == direction and KIND_POLICY[item.kind].base > 0
    ]
    return [(item, value) for item, value in scored if value > 0]


def _supersedes(clean: Evidence, limited: Evidence) -> bool:
    """A newer clean item whose base weight is not lower than the limited one's."""
    return (
        clean.observed_at > limited.observed_at
        and KIND_POLICY[clean.kind].base >= KIND_POLICY[limited.kind].base
    )


def _open(items: list[Evidence], kind: EvidenceKind, closer: EvidenceKind) -> list[Evidence]:
    """Items of a STATE kind that no later ``closer`` has ended."""
    closers = [item.observed_at for item in items if item.kind == closer]
    return [
        item
        for item in items
        if item.kind == kind and not any(closed > item.observed_at for closed in closers)
    ]


def _expired_limits(items: list[Evidence], now: datetime) -> list[Evidence]:
    """Dated limits whose date has passed."""
    return [
        item
        for item in items
        if KIND_POLICY[item.kind].direction == "limited"
        and KIND_POLICY[item.kind].base > 0
        and item.expires_at is not None
        and now >= item.expires_at
    ]


def _latest(items: Iterable[Evidence], kinds: frozenset[EvidenceKind]) -> datetime | None:
    stamps = [item.observed_at for item in items if item.kind in kinds]
    return max(stamps) if stamps else None


def _ending(limited: list[Evidence]) -> datetime | None:
    """When the limited state ends if nothing else happens: every item dated, the latest date."""
    dates = [item.expires_at for item in limited]
    return None if any(d is None for d in dates) else max(d for d in dates if d is not None)


def _limited(
    limited: list[_Scored], cleans: list[_Scored], now: datetime, cfg: SpamIntelSettings
) -> Verdict:
    decider, value = _strongest(limited)
    contradiction = any(c.observed_at > item.observed_at for c, _ in cleans for item, _ in limited)
    undated_old = decider.expires_at is None and now - decider.observed_at >= timedelta(
        days=cfg.limited_recheck_days
    )
    reason: VerdictReason | None = (
        "contradiction" if contradiction else ("recheck_due" if undated_old else None)
    )
    return Verdict(
        status="limited",
        confidence=bucket(value),
        valid_until=_ending([item for item, _ in limited]),
        primary_kind=decider.kind,
        needs_probe=reason is not None,
        contradiction=contradiction,
        reason=reason,
    )


def _clean(cleans: list[_Scored]) -> Verdict:
    decider, value = _strongest(cleans)
    return Verdict(
        status="clean",
        confidence=bucket(value),
        primary_kind=decider.kind,
        reason="stale" if value < STALE_BELOW else None,
    )


def _unknown(items: list[Evidence], now: datetime, expired: list[Evidence]) -> Verdict:
    if expired:
        return Verdict(status="unknown", needs_probe=True, reason="limit_expired")
    answers = [
        item
        for item in items
        if item.kind in {"spambot_reviewing", "spambot_unreadable"}
        and (item.expires_at is None or now < item.expires_at)
    ]
    if not answers:
        return Verdict(status="unknown", reason="no_evidence")
    newest = max(answers, key=lambda item: (item.observed_at, item.kind))
    return Verdict(
        status="unknown",
        reason="reviewing" if newest.kind == "spambot_reviewing" else "unreadable",
    )


def _spam_verdict(items: list[Evidence], now: datetime, cfg: SpamIntelSettings) -> Verdict:
    cleans = _active(items, now, "clean")
    limited = [
        (item, value)
        for item, value in _active(items, now, "limited")
        if not any(_supersedes(clean, item) for clean, _ in cleans)
    ]
    if limited:
        return _limited(limited, cleans, now, cfg)
    expired = _expired_limits(items, now)
    # A dated limit that ran out after a clean reading blocks that reading: we do not know
    # what the account is now, and only a new answer can say.
    cleans = [
        (item, value)
        for item, value in cleans
        if not any(old.observed_at >= item.observed_at for old in expired)
    ]
    return _clean(cleans) if cleans else _unknown(items, now, expired)


def fold(evidence: Iterable[Evidence], now: datetime, cfg: SpamIntelSettings) -> Verdict:
    """The verdict the evidence supports at ``now`` (``now`` must be timezone-aware)."""
    items = list(evidence)
    restricted = _open(items, "tos_restricted", "tos_cleared")
    flags: dict[str, object] = {
        "last_spambot_at": _latest(items, SPAMBOT_KINDS),
        "tos_restricted": bool(restricted),
        "tos_reason": (
            max(restricted, key=lambda item: (item.observed_at, item.detail or "")).detail
            if restricted
            else None
        ),
        "weights_used": {item.kind: KIND_POLICY[item.kind].base for item in items},
    }
    if _open(items, "freeze_seen", "freeze_cleared"):
        base = Verdict(status="frozen", confidence="high", primary_kind="freeze_seen")
        return base.model_copy(update={**flags, "reason": "frozen"})
    return _spam_verdict(items, now, cfg).model_copy(update=flags)
