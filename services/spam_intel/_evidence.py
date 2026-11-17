"""Passive evidence: record what normal work shows, fold it, read it back (design 5.9, 5.6).

The two calls other code uses (also re-exported by ``services.spam_intel``):

* :func:`record_evidence` — cheap, never raises into the caller, switched by
  ``SPAM_INTEL__PASSIVE_SIGNALS`` (and the gateway master switch). It stores one observation
  (coalescing a burst into a count), prunes, re-folds the account's evidence and stores the
  projection in the new columns of ``account_spam_status``.
* :func:`verdicts_for` — a verdict for each account, from the stored projection, with the clock
  applied to expiry. DB only, never Telegram. An account that has no projection yet reads from its
  legacy reading, so a reader can switch to it before any evidence exists.

The fold is a SHADOW by default: it is stored and logged (``spam_intel_evidence``,
``spam_intel_verdict_changed``) but ``account_spam_status.status`` / ``detail`` / ``checked_at``
stay what a real @SpamBot answer wrote. With ``SPAM_INTEL__PASSIVE_DECISIONS=true`` a NON-clean
fold (limited, frozen, an expired limit) is also written there. A passive ``clean`` is never
written, and nothing here lifts a spam block: only a persisted @SpamBot ``clean`` does, and that
stays on the @SpamBot path (``services.spam_block_lift``, owner decision D4).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from core.repositories.spam_evidence import list_evidence, prune_evidence
from core.repositories.spam_evidence import record_evidence as store_evidence
from core.repositories.spam_status import list_projections_by_ids, store_projection
from core.repositories.spam_status import upsert_spam_status as write_legacy_status
from schemas.spam_evidence import DETAIL_MAX_CHARS, Evidence, Verdict
from schemas.spam_status import SpamStatusVerdict
from services.spam_intel._evidence_policy import KIND_POLICY, SPAMBOT_KINDS, default_expiry
from services.spam_intel._fold import fold
from services.spam_intel._readings import LEGACY_SOURCE, expire, reading_evidence

if TYPE_CHECKING:
    from collections.abc import Iterable

    from schemas.spam_evidence import EvidenceKind, StoredProjection
    from schemas.spam_status import SpamStatusKind

logger = logging.getLogger(__name__)

_NAME_MAX_CHARS = 64


def now() -> datetime:
    """The evidence layer's one clock — patched by tests."""
    return datetime.now(UTC)


def _clip(text: str | None, limit: int) -> str | None:
    return None if text is None else text[:limit]


async def record_evidence(  # noqa: PLR0913 - the public asker; every knob is a keyword.
    account_id: str,
    kind: EvidenceKind,
    *,
    source: str,
    operation: str | None = None,
    detail: str | None = None,
    observed_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> None:
    """Record one observation about an account. Never raises: a failure is logged by class name.

    ``source`` names where it was raised (``execute``, ``session_check``, ``outreach``);
    ``operation`` the action family if any; ``detail`` a short redacted fact, truncated to 200
    characters (never a quoted third-party text). ``observed_at`` defaults to now; ``expires_at``
    to the kind's own time to live. A burst of the same account/kind/source inside
    ``evidence_coalesce_minutes`` is one row with a count. Does nothing when
    ``SPAM_INTEL__PASSIVE_SIGNALS`` or ``SPAM_INTEL__ENABLED`` is off.
    """
    intel = settings.spam_intel
    if not (intel.enabled and intel.passive_signals):
        return
    try:
        seen = observed_at or now()
        item = Evidence(
            account_id=account_id,
            kind=kind,
            direction=KIND_POLICY[kind].direction,
            observed_at=seen,
            expires_at=expires_at or default_expiry(kind, seen),
            source=source[:_NAME_MAX_CHARS],
            operation=_clip(operation, _NAME_MAX_CHARS),
            detail=_clip(detail, DETAIL_MAX_CHARS),
        )
        await _store(item)
    except Exception as exc:  # noqa: BLE001 - a signal must never fail the action that raised it.
        logger.warning("spam evidence not recorded: %s", type(exc).__name__)


async def record_reading(
    account_id: str,
    status: SpamStatusKind,
    detail: str | None,
    at: datetime,
) -> None:
    """A real @SpamBot reading the gateway just stored becomes evidence (never raises)."""
    try:
        evidence = reading_evidence(account_id, status, detail, at)
    except Exception as exc:  # noqa: BLE001 - recording must never fail the probe.
        logger.warning("spam reading not recorded: %s", type(exc).__name__)
        return
    if evidence is not None:
        await record_evidence(
            account_id,
            evidence.kind,
            source=evidence.source,
            operation=evidence.operation,
            detail=evidence.detail,
            observed_at=evidence.observed_at,
            expires_at=evidence.expires_at,
        )


async def _store(item: Evidence) -> None:
    intel = settings.spam_intel
    at = now()
    window = timedelta(minutes=intel.evidence_coalesce_minutes)
    if not await store_evidence(item, window=window):
        return  # joined an older row: nothing new to fold
    await log_event(
        "INFO",
        "spam_intel_evidence",
        account_id=item.account_id,
        extra={"kind": item.kind, "direction": item.direction, "source": item.source},
    )
    await prune_evidence(
        at - timedelta(days=intel.evidence_retention_days),
        intel.evidence_max_per_account,
        account_id=item.account_id,
    )
    await _refold(item.account_id, at)


async def _refold(account_id: str, at: datetime) -> None:
    intel = settings.spam_intel
    verdict = fold(await list_evidence(account_id), at, intel)
    stored = (await list_projections_by_ids([account_id])).get(account_id)
    previous = None if stored is None else stored.verdict
    await store_projection(account_id, verdict, at)
    if previous is None or (previous.status, previous.primary_kind) != (
        verdict.status,
        verdict.primary_kind,
    ):
        await log_event(
            "INFO",
            "spam_intel_verdict_changed",
            account_id=account_id,
            extra={
                "from": None if previous is None else previous.status,
                "to": verdict.status,
                "primary_kind": verdict.primary_kind,
                "reason": verdict.reason,
                "shadow": not intel.passive_decisions,
            },
        )
    if intel.passive_decisions:
        await _mirror(account_id, verdict, None if stored is None else stored.legacy, at)


def _legacy_view(verdict: Verdict) -> tuple[SpamStatusKind, str] | None:
    """What a passive verdict writes into the legacy columns; ``None`` = nothing (never a clean)."""
    if verdict.status == "frozen":
        return "unknown", "frozen"  # design D12
    if verdict.status == "limited" and verdict.primary_kind is not None:
        return "limited", verdict.primary_kind
    if verdict.reason == "limit_expired":
        return "unknown", "limit_expired"
    return None


def _already(legacy: SpamStatusVerdict | None, status: SpamStatusKind, detail: str) -> bool:
    """The legacy row already says it (a limited one need not match the kind that caused it)."""
    if legacy is None or legacy.status != status:
        return False
    return status == "limited" or legacy.detail == detail


async def _mirror(
    account_id: str, verdict: Verdict, legacy: SpamStatusVerdict | None, at: datetime
) -> None:
    """``passive_decisions``: write a non-clean fold into the legacy status, if it differs."""
    if verdict.primary_kind in SPAMBOT_KINDS:
        return  # a real answer decided, and the probe already wrote it
    view = _legacy_view(verdict)
    if view is None:
        return
    status, detail = view
    if _already(legacy, status, detail):
        return
    await write_legacy_status(
        SpamStatusVerdict(
            account_id=account_id, status=status, detail=detail, checked_at=at.isoformat()
        )
    )


def _from_legacy(row: StoredProjection, at: datetime) -> Verdict:
    """A verdict for an account whose last word is a legacy reading (no projection, or older)."""
    legacy = row.legacy
    evidence = None
    if legacy is not None:
        stamp = datetime.fromisoformat(legacy.checked_at)
        stamp = stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)
        evidence = reading_evidence(
            legacy.account_id, legacy.status, legacy.detail, stamp, source=LEGACY_SOURCE
        )
    return fold([] if evidence is None else [evidence], at, settings.spam_intel)


def _newer_legacy(row: StoredProjection) -> bool:
    """The legacy reading was written after the last fold (a direct write bypassed evidence)."""
    if row.legacy is None:
        return False
    if row.verdict is None or row.folded_at is None:
        return True
    stamp = datetime.fromisoformat(row.legacy.checked_at)
    stamp = stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)
    return stamp > row.folded_at


def _read(row: StoredProjection | None, at: datetime) -> Verdict:
    if row is None:
        return Verdict(status="unknown", reason="no_evidence")
    stored = row.verdict
    if stored is not None and not _newer_legacy(row):
        return expire(stored, at)
    derived = _from_legacy(row, at)
    if stored is not None:
        # Flags and a freeze are not readings: they survive a newer reading.
        derived = derived.model_copy(
            update={"tos_restricted": stored.tos_restricted, "tos_reason": stored.tos_reason}
        )
        if stored.status == "frozen":
            return stored
    return expire(derived, at)


async def verdicts_for(account_ids: Iterable[str]) -> dict[str, Verdict]:
    """A verdict for every account asked about, from what is stored. No Telegram, ever.

    The stored projection is as of its fold: ``confidence`` is not re-aged here, only a dated
    limit that has ended is applied (it reads ``unknown``, reason ``limit_expired``, with
    ``needs_probe``). An account with no evidence and no reading reads ``unknown`` /
    ``no_evidence``: never ``clean``.
    """
    ids = list(dict.fromkeys(account_ids))
    stored = await list_projections_by_ids(ids)
    at = now()
    return {account_id: _read(stored.get(account_id), at) for account_id in ids}
