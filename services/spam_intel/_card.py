"""What the account card and the Accounts page show about spam (docs/spam-intel.md 5.10).

Read-only except :func:`check_for_operator`, which is the Spam-check button through the gateway.
Everything the card shows is derived from stored data in a handful of bulk queries (the stored
Stage 2 projection through :func:`verdicts_for`, the probe ledger, the appeal rows), never one
query per account, and never from Telegram. The SPA decides nothing: whether a click needs the
operator's confirmation, when the next check is possible, and which source decided the verdict
are all computed here.

``tos_restricted`` is carried beside the verdict as its own flag and reason; it never enters
the spam chip.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.repositories.spam_appeals import list_latest_appeals
from core.repositories.spam_probe_ledger import fleet_probe_use, list_probe_states, probe_activity
from schemas.spam_card import (
    ProbeActivity,
    SpamBudgetStatus,
    SpamCardSignals,
    SpamCheckResult,
    SpamSource,
)
from services.spam_intel._appeal import open_appeal
from services.spam_intel._evidence import verdicts_for
from services.spam_intel._gateway import operator_check
from services.spam_intel._policy import parse_moment

if TYPE_CHECKING:
    from collections.abc import Iterable

    from schemas.spam_appeals import AppealStatus
    from schemas.spam_evidence import EvidenceKind, Verdict
    from schemas.spam_intel import ProbeState
    from schemas.spam_status import SpamStatusVerdict

# The refusal the SPA translates: the last probe is under ``out_of_turn_hours`` old and the
# click was not confirmed. Raised by :func:`check_for_operator`; the route answers 409.
NEEDS_CONFIRMATION = "needs_confirmation"

_PROBE_WINDOW = timedelta(days=7)
_DAY = timedelta(hours=24)

_SOURCE: dict[EvidenceKind, SpamSource] = {
    "spambot_limited": "spambot",
    "spambot_clean": "spambot",
    "spambot_unreadable": "spambot",
    "spambot_reviewing": "spambot",
    "peer_flood_send": "peer_flood",
    "peer_flood_other": "peer_flood",
    "user_banned_in_channel": "chat_ban",
    "freeze_seen": "freeze",
}


class OutOfTurnConfirmationError(Exception):
    """The click is out of turn (the last probe is under a day old) and was not confirmed."""

    code = NEEDS_CONFIRMATION


def now() -> datetime:
    """The card's one clock — patched by tests."""
    return datetime.now(UTC)


def source_of(verdict: Verdict) -> SpamSource:
    """Where the verdict's deciding observation came from; ``none`` when it names no source."""
    if verdict.primary_kind is None:
        return "none"
    return _SOURCE.get(verdict.primary_kind, "none")


def _observed_at(verdict: Verdict, source: SpamSource) -> datetime | None:
    """When the deciding observation was made (a projection older than the column: @SpamBot's)."""
    if verdict.primary_observed_at is not None:
        return verdict.primary_observed_at
    return verdict.last_spambot_at if source == "spambot" else None


def next_probe_at(
    activity: ProbeActivity | None,
    state: ProbeState | None,
    at: datetime,
) -> datetime | None:
    """The earliest instant the budget lets another probe of the account go, or ``None`` (now).

    The coalescing window after its last probe, pushed to the end of the backoff an unreadable
    answer started. The operator's click is bound by the window alone (a refused or postponed
    answer says its own ``retry_at``).
    """
    intel = settings.spam_intel
    candidates: list[datetime] = []
    last = parse_moment(None if activity is None else activity.last_started_at)
    if last is not None:
        candidates.append(last + timedelta(minutes=intel.coalesce_minutes))
    backoff = parse_moment(None if state is None else state.next_allowed_at)
    if backoff is not None:
        candidates.append(backoff)
    later = max(candidates, default=None)
    return later if later is not None and later > at else None


def out_of_turn(activity: ProbeActivity | None, at: datetime) -> bool:
    """A click now is out of turn: past the coalescing window, inside ``out_of_turn_hours``."""
    intel = settings.spam_intel
    last = parse_moment(None if activity is None else activity.last_started_at)
    if last is None:
        return False
    settled = last + timedelta(minutes=intel.coalesce_minutes)
    return settled <= at < last + timedelta(hours=intel.out_of_turn_hours)


def _appeal_status(closed_at: datetime | None) -> AppealStatus:
    return "open" if closed_at is None else "closed"


async def card_signals(account_ids: Iterable[str]) -> dict[str, SpamCardSignals]:
    """The card's spam signals for every account asked about, from stored data (bulk reads)."""
    ids = list(dict.fromkeys(account_ids))
    if not ids:
        return {}
    at = now()
    verdicts = await verdicts_for(ids)
    activity = await probe_activity(ids, (at - _PROBE_WINDOW).isoformat())
    states = await list_probe_states(ids)
    for appeal in (await list_latest_appeals(ids)).values():
        if appeal.closed_at is None:
            await open_appeal(appeal.account_id)  # closes one a stored clean already ended
    appeals = await list_latest_appeals(ids)
    result: dict[str, SpamCardSignals] = {}
    for account_id in ids:
        verdict = verdicts[account_id]
        source = source_of(verdict)
        seen = activity.get(account_id)
        appeal = appeals.get(account_id)
        result[account_id] = SpamCardSignals(
            verdict=verdict.status,
            confidence=verdict.confidence,
            source=source,
            observed_at=_observed_at(verdict, source),
            valid_until=verdict.valid_until,
            next_probe_at=next_probe_at(seen, states.get(account_id), at),
            last_probe_at=parse_moment(None if seen is None else seen.last_started_at),
            probes_7d=0 if seen is None else seen.count_7d,
            out_of_turn=out_of_turn(seen, at),
            tos_restricted=verdict.tos_restricted,
            tos_reason=verdict.tos_reason,
            appeal_status="none" if appeal is None else _appeal_status(appeal.closed_at),
            appeal_filed_at=None if appeal is None else appeal.filed_at,
            appeal_filed_by=None if appeal is None else appeal.filed_by,
        )
    return result


async def budget_status() -> SpamBudgetStatus:
    """The fleet's real probes and the operator's own over the last 24 h, against the caps."""
    intel = settings.spam_intel
    total, operator = await fleet_probe_use((now() - _DAY).isoformat())
    return SpamBudgetStatus(
        probes_24h=total,
        soft_cap=intel.fleet_soft_cap,
        hard_cap=intel.fleet_hard_cap,
        out_of_turn_24h=operator,
        out_of_turn_warn_at=intel.out_of_turn_warn_count,
        daily_cap_reached=total >= intel.fleet_soft_cap,
        out_of_turn_warned=operator >= intel.out_of_turn_warn_count,
    )


async def _next_allowed(verdict: SpamStatusVerdict) -> str | None:
    """When the account may be probed again, after this answer (``retry_at`` if it was held)."""
    if verdict.retry_at is not None:
        return verdict.retry_at
    at = now()
    activity = await probe_activity([verdict.account_id], (at - _PROBE_WINDOW).isoformat())
    states = await list_probe_states([verdict.account_id])
    later = next_probe_at(activity.get(verdict.account_id), states.get(verdict.account_id), at)
    return None if later is None else later.isoformat()


async def check_for_operator(account_id: str, *, confirm_out_of_turn: bool) -> SpamCheckResult:
    """The Spam-check button: the gateway's answer, plus whether it probed and when to ask again.

    Out of turn (the last probe is past the 30-minute window but under ``out_of_turn_hours`` old)
    an unconfirmed click is refused with :class:`OutOfTurnConfirmationError`; the minimum, the
    operator's daily cap and the fleet caps stay the gateway's.
    """
    verdict = await operator_check(account_id, confirm_out_of_turn=confirm_out_of_turn)
    if verdict.served_from == "refused" and verdict.reason == NEEDS_CONFIRMATION:
        raise OutOfTurnConfirmationError
    return SpamCheckResult(
        **verdict.model_dump(),
        probed=verdict.served_from == "probe",
        next_allowed_at=await _next_allowed(verdict),
    )
