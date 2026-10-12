"""Internal account Trust Score — our own 0-100 health aggregate.

Explicitly NOT a mirror of any Telegram-published metric (Telegram does not
publish one). Aggregates signals we already store — session status, spam-limit
verdict, quarantine / flood history, proxy + geo consistency, and account age —
into a single score and band for gating and UI display. All weights live in
``settings.trust`` (no magic numbers).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    fetch_account,
    fetch_device_fingerprint,
    fetch_warming_flood_summary,
    fetch_warming_state,
    get_spam_status,
)
from core.phone_geo import evaluate_geo
from schemas.trust import TrustBand, TrustScore, TrustSignals

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.spam_status import SpamStatusVerdict
    from schemas.warming import WarmingStateRecord
    from schemas.warming_flood import WarmingFloodSummary

_SECONDS_PER_HOUR = 3600
_SECONDS_PER_DAY = 86400


def _band(score: int) -> TrustBand:
    trust = settings.trust
    if score >= trust.excellent_min:
        return "excellent"
    if score >= trust.good_min:
        return "good"
    if score >= trust.watch_min:
        return "watch"
    if score >= trust.at_risk_min:
        return "at_risk"
    return "critical"


def _flood_history_penalty(signals: TrustSignals, now: datetime) -> int:
    """Lifetime spam blocks, fading linearly to zero since the latest one."""
    trust = settings.trust
    if signals.flood_total <= 0 or trust.flood_history_decay_days <= 0:
        return 0
    last = _parse_utc(signals.last_flood_at)
    if last is None:
        return 0
    elapsed_days = max(0.0, (now - last).total_seconds() / _SECONDS_PER_DAY)
    remaining = 1.0 - elapsed_days / trust.flood_history_decay_days
    if remaining <= 0:
        return 0
    full = min(
        trust.penalty_flood_history_max,
        trust.penalty_flood_history_each * signals.flood_total,
    )
    return round(full * remaining)


def compute_trust_score(signals: TrustSignals, now: datetime | None = None) -> TrustScore:
    """Combine account signals into a 0-100 trust verdict (pure but for the clock)."""
    trust = settings.trust
    score = 100
    reasons: list[str] = []

    if signals.account_status != "alive":
        score -= trust.penalty_not_alive
        reasons.append(f"status {signals.account_status}")
    if signals.spam_status == "limited":
        score -= trust.penalty_spam_limited
        reasons.append("spam-limited")
    # "unknown" deliberately does NOT dock the score: it means "not probed yet"
    # rather than "risky". Operators see it in the spam badge and can refresh
    # from the card; no need to mix a missing-data marker into the verdict.
    if signals.quarantine_count > 0:
        score -= trust.penalty_quarantine_each * signals.quarantine_count
        reasons.append(f"quarantined x{signals.quarantine_count}")
    if signals.flood_active:
        score -= trust.penalty_flood_active
        reasons.append("recent flood")
    flood_history = _flood_history_penalty(signals, now or datetime.now(UTC))
    if flood_history > 0:
        score -= flood_history
        reasons.append(f"floods x{signals.flood_total}")
    if signals.geo_status == "mismatch":
        score -= trust.penalty_geo_mismatch
        reasons.append("geo mismatch")
    elif signals.geo_status == "unknown":
        score -= trust.penalty_geo_unknown
        reasons.append("geo unknown")
    if signals.proxy_status == "failed":
        score -= trust.penalty_proxy_failed
        reasons.append("proxy failed")
    if trust.new_account_hours > 0 and signals.age_hours < trust.new_account_hours:
        # Linear ramp: full penalty at 0h, zero penalty at new_account_hours.
        # Avoids the cliff where 47h costs 10 and 49h costs 0.
        ramp = 1.0 - (signals.age_hours / trust.new_account_hours)
        score -= round(trust.penalty_new_account * ramp)
        reasons.append("new account")

    score = max(0, min(100, score))
    return TrustScore(
        account_id=signals.account_id,
        score=score,
        band=_band(score),
        reasons=reasons,
    )


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _age_hours(created_at: str, now: datetime) -> float:
    try:
        created = datetime.fromisoformat(created_at)
    except ValueError:
        return 0.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return max(0.0, (now - created).total_seconds() / _SECONDS_PER_HOUR)


def flood_active(flood_wait_until: str | None, now: datetime) -> bool:
    """Is a persisted flood-wait deadline still in the future?

    Public because channel discovery also refuses to search on a flood-waiting
    account (a second consumer is when a helper stops being private).
    """
    if not flood_wait_until:
        return False
    try:
        until = datetime.fromisoformat(flood_wait_until)
    except ValueError:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    return until > now


def account_trust_score_from(  # noqa: PLR0913 - already-loaded signals, one per source.
    account: AccountRead,
    record: WarmingStateRecord | None,
    spam: SpamStatusVerdict | None,
    lang_code: str | None,
    now: datetime,
    floods: WarmingFloodSummary | None = None,
) -> TrustScore:
    """Compute a Trust Score from already-loaded signals — no DB I/O.

    Lets a batch caller (the warming board) score many accounts from one set of
    bulk-loaded rows instead of re-querying account/state/spam/fingerprint per card.
    Every caller passes these by keyword; the bare ``*`` went only because the
    aislop parameter gate counts it as a seventh parameter.
    """
    geo = evaluate_geo(
        phone=account.phone,
        proxy_country=account.proxy_country_code,
        lang_code=lang_code,
    )
    return compute_trust_score(
        TrustSignals(
            account_id=account.account_id,
            account_status=account.status,
            spam_status=spam.status if spam else "unknown",
            quarantine_count=record.quarantine_count if record else 0,
            flood_active=flood_active(record.flood_wait_until if record else None, now),
            geo_status=geo.status,
            proxy_status=account.proxy_status,
            age_hours=_age_hours(account.created_at, now),
            flood_total=floods.flood_total if floods else 0,
            last_flood_at=floods.last_flood_at if floods else None,
        ),
        now,
    )


async def account_trust_score(account_id: str) -> TrustScore:
    """Gather an account's current signals and compute its Trust Score."""
    account = await fetch_account(account_id)
    if account is None:
        return TrustScore(
            account_id=account_id,
            score=0,
            band="critical",
            reasons=["unknown account"],
        )
    record = await fetch_warming_state(account_id)
    spam = await get_spam_status(account_id)
    fingerprint = await fetch_device_fingerprint(account_id)
    return account_trust_score_from(
        account=account,
        record=record,
        spam=spam,
        lang_code=fingerprint.system_lang_code if fingerprint else None,
        now=datetime.now(UTC),
        floods=await fetch_warming_flood_summary(account_id),
    )
