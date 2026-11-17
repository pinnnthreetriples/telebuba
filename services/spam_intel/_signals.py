"""The receiver of ``core``'s passive signals: what the gateways saw becomes evidence.

``core.spam_signals`` is the port (``core`` cannot import ``services``); the app registers
:func:`receive` at startup through :func:`install_signal_sink`. Two rules apply before a signal
reaches :func:`record_evidence`:

* A CLOSER (``freeze_cleared``, ``tos_cleared``) is only worth a row while its opposite is open.
  The session check reports "no freeze" and "not restricted" every time it runs; recording each
  would store a row per account that never was frozen. It is read from the stored projection, no
  Telegram.
* A SHADOW success (weight 0: a group send, a join, a read that worked) is kept at most once a
  day per account and kind. It moves no verdict, so one sighting a day is all the Stage 2
  comparison with @SpamBot needs, and it keeps a busy account from crowding the real evidence out
  of the per-account row cap.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from core.spam_signals import set_signal_sink
from services.spam_intel import _evidence
from services.spam_intel._evidence_policy import KIND_POLICY

if TYPE_CHECKING:
    from datetime import datetime

    from schemas.spam_evidence import EvidenceKind, Verdict

# Reported at most this often per account and shadow kind (in process memory; a restart resets it).
_SHADOW_EVERY = timedelta(hours=24)
_shadow_seen: dict[tuple[str, EvidenceKind], datetime] = {}


def _is_shadow(kind: EvidenceKind) -> bool:
    policy = KIND_POLICY[kind]
    return policy.direction == "clean" and policy.base == 0


def _is_open(kind: EvidenceKind, verdict: Verdict) -> bool:
    """Whether the state a closer ends is on, as the stored projection has it."""
    if kind == "freeze_cleared":
        return verdict.status == "frozen"
    return verdict.tos_restricted


def _too_soon(account_id: str, kind: EvidenceKind) -> bool:
    """Shadow success seen recently? Otherwise this sighting is the one of its day."""
    at = _evidence.now()
    last = _shadow_seen.get((account_id, kind))
    if last is not None and at - last < _SHADOW_EVERY:
        return True
    _shadow_seen[account_id, kind] = at
    return False


async def receive(
    account_id: str,
    kind: EvidenceKind,
    *,
    source: str,
    operation: str | None = None,
    detail: str | None = None,
) -> None:
    """Record one signal from ``core`` as evidence, unless it carries nothing (see the module)."""
    if _is_shadow(kind) and _too_soon(account_id, kind):
        return
    if kind in {"freeze_cleared", "tos_cleared"}:
        verdict = (await _evidence.verdicts_for([account_id]))[account_id]
        if not _is_open(kind, verdict):
            return
    await _evidence.record_evidence(
        account_id, kind, source=source, operation=operation, detail=detail
    )


def install_signal_sink() -> None:
    """Register :func:`receive` with the port. Calling it again changes nothing."""
    set_signal_sink(receive)


def reset_for_tests() -> None:
    """Forget the shadow throttle."""
    _shadow_seen.clear()
