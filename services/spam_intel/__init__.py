"""Spam intel: the single gateway to an account's spam status (docs/spam-intel.md).

Package root kept thin: the behaviour is in ``_gateway`` (the ask, single-flight, the
ledger) and ``_policy`` (the pure budget); the read-only dialog sweep is ``_history``.
Passive evidence (Stage 2): ``record_evidence`` records one observation, ``verdicts_for`` reads
verdicts from the stored projection, ``fold`` is the pure function between them
(``_evidence``, ``_fold``, ``_evidence_policy``). With ``passive_decisions`` on, readers go
through ``decision_status(es)`` (``_decisions``) and the ask may be answered from evidence
(``_served``, ``_adaptive``); off, the fold is compared with real answers (``_shadow``).
``install_signal_sink`` registers the receiver (``_signals``) that turns what ``core``'s gateways
saw into that evidence.
"""

from __future__ import annotations

from services.spam_intel._decisions import decision_status, decision_statuses
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
from services.spam_intel._limit_date import end_date, limit_end_date
from services.spam_intel._shadow import shadow_agreement
from services.spam_intel._signals import install_signal_sink

__all__ = [
    "CALLER_PURPOSE",
    "ask",
    "bound_asker",
    "caller_scope",
    "decision_status",
    "decision_statuses",
    "end_date",
    "fold",
    "install_signal_sink",
    "limit_end_date",
    "operator_check",
    "parse_retry",
    "record_evidence",
    "record_peer_flood_limited",
    "shadow_agreement",
    "shutdown_history_sweep",
    "start_history_sweep",
    "verdicts_for",
]
