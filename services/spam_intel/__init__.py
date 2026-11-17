"""Spam intel: the single gateway to an account's spam status (docs/spam-intel.md).

Package root kept thin: the behaviour is in ``_gateway`` (the ask, single-flight, the
ledger) and ``_policy`` (the pure budget); the read-only dialog sweep is ``_history``.
Passive evidence (Stage 2): ``record_evidence`` records one observation, ``verdicts_for`` reads
verdicts from the stored projection, ``fold`` is the pure function between them
(``_evidence``, ``_fold``, ``_evidence_policy``).
"""

from __future__ import annotations

from services.spam_intel._evidence import record_evidence, verdicts_for
from services.spam_intel._external import record_peer_flood_limited
from services.spam_intel._fold import fold
from services.spam_intel._gateway import (
    CALLER_PURPOSE,
    ask,
    bound_asker,
    caller_scope,
    operator_check,
    parse_retry,
)
from services.spam_intel._history import shutdown_history_sweep, start_history_sweep

__all__ = [
    "CALLER_PURPOSE",
    "ask",
    "bound_asker",
    "caller_scope",
    "fold",
    "operator_check",
    "parse_retry",
    "record_evidence",
    "record_peer_flood_limited",
    "shutdown_history_sweep",
    "start_history_sweep",
    "verdicts_for",
]
