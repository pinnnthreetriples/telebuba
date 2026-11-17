"""Builders shared by the fold tests: evidence at a fixed clock, and the default settings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core._config_spam_intel import SpamIntelSettings
from schemas.spam_evidence import Evidence, EvidenceKind
from services.spam_intel._evidence_policy import KIND_POLICY

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
CFG = SpamIntelSettings()


def ev(
    kind: EvidenceKind,
    ago_hours: float = 0.0,
    *,
    expires_in_hours: float | None = None,
    detail: str | None = None,
    source: str = "test",
) -> Evidence:
    """One item observed ``ago_hours`` before ``NOW``; ``expires_in_hours`` is relative to NOW."""
    return Evidence(
        account_id="a1",
        kind=kind,
        direction=KIND_POLICY[kind].direction,
        observed_at=NOW - timedelta(hours=ago_hours),
        expires_at=None if expires_in_hours is None else NOW + timedelta(hours=expires_in_hours),
        source=source,
        detail=detail,
    )
