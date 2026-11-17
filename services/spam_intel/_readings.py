"""A stored @SpamBot reading as evidence, and a stored projection as a verdict at a given time.

Pure. Two jobs the evidence layer shares with the gateway:

* :func:`reading_evidence` turns a ``clean`` / ``limited`` / ``unknown`` reading (what the
  probe stores in ``account_spam_status``) into the evidence it stands for. The gateway records
  it when a probe stores a reading; ``verdicts_for`` uses it to read an account that has no
  projection yet (every account before Stage 2) without a backfill migration.
* :func:`expire` applies the clock to a stored verdict: a dated limit that ran out reads
  ``unknown`` with reason ``limit_expired``.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - used in a runtime annotation of a model call.

from schemas.spam_evidence import Evidence, EvidenceKind, Verdict
from schemas.spam_status import SpamStatusKind  # noqa: TC001 - annotation read by pydantic.
from services._spam_parse import AMBIGUOUS_DETAIL, CHECKING_DETAIL, UNRECOGNIZED_DETAIL
from services.spam_intel._evidence_policy import KIND_POLICY, default_expiry
from services.spam_intel._limit_date import end_date, parse_limit_expiry

SPAMBOT_SOURCE = "spambot"
LEGACY_SOURCE = "legacy_status"
# The detail the legacy peer-flood write (masstagging) stores: a passive finding, not a reading.
_PEER_FLOOD_DETAIL = "peer_flood"
_UNREADABLE_DETAILS = frozenset({None, UNRECOGNIZED_DETAIL, AMBIGUOUS_DETAIL})


def reading_evidence(
    account_id: str,
    status: SpamStatusKind,
    detail: str | None,
    at: datetime,
    *,
    source: str = SPAMBOT_SOURCE,
) -> Evidence | None:
    """The evidence a reading stands for, or ``None`` when it carries none.

    A limited reading keeps only the END DATE it named (ISO), never the reply's own words.
    """
    kind: EvidenceKind
    expires_at = None
    stored: str | None = None
    if status == "clean":
        kind = "spambot_clean"
    elif status == "limited":
        expires_at = parse_limit_expiry(detail)
        ends = end_date(expires_at)
        stored = None if ends is None else ends.isoformat()
        kind = "peer_flood_send" if detail == _PEER_FLOOD_DETAIL else "spambot_limited"
    elif detail == CHECKING_DETAIL:
        kind = "spambot_reviewing"
    elif detail in _UNREADABLE_DETAILS:
        kind, stored = "spambot_unreadable", detail
    else:
        return None  # a transport failure or a state of ours (``frozen``): not a reading
    evidence = Evidence.model_validate(
        {
            "account_id": account_id,
            "kind": kind,
            "direction": KIND_POLICY[kind].direction,
            "observed_at": at,
            "expires_at": expires_at,
            "source": source,
            "operation": "probe" if source == SPAMBOT_SOURCE else None,
            "detail": stored,
        }
    )
    if evidence.expires_at is None:
        evidence.expires_at = default_expiry(evidence.kind, at)
    return evidence


def expire(verdict: Verdict, at: datetime) -> Verdict:
    """The stored verdict as it reads at ``at``: a dated limit that has ended is ``unknown``."""
    if verdict.status != "limited" or verdict.valid_until is None or at < verdict.valid_until:
        return verdict
    return verdict.model_copy(
        update={
            "status": "unknown",
            "confidence": "low",
            "valid_until": None,
            "primary_kind": None,
            "needs_probe": True,
            "contradiction": False,
            "reason": "limit_expired",
        }
    )
