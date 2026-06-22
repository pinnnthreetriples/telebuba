"""Warming kanban board read model.

Builds the board state the UI polls: one card per account with its warming
state plus bulk-loaded health signals (trust/spam/age-ramp). All DB rows are
fetched once here — there is no per-card N+1.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from core.db import (
    list_accounts,
    list_device_fingerprints,
    list_spam_statuses,
    list_warming_channels,
    list_warming_states,
)
from core.phone_geo import country_for_phone
from schemas.warming import (
    WarmingAccountState,
    WarmingBoardState,
    WarmingPhase,
    WarmingState,
    WarmingSummary,
    is_warming,
    warming_health,
)
from services.trust import account_trust_score_from
from services.warming.pacing import _account_age_hours, compute_intensity, evaluate_readiness
from services.warming.settings_store import load_settings

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.warming import WarmingReadiness, WarmingStateRecord

_HOURS_PER_DAY = 24

# Russian labels for the lifecycle phases shown on the kanban card.
#
# Wording rule: nouns/adjectives, never gerunds — the phase is a *stage of
# maturity*, not an action. The state pill (`state="active"` → «Прогрев»)
# already occupies the verb space; phase labels stay clearly different so
# an idle account in the warming-age band doesn't look like it's "currently
# being warmed".
_PHASE_LABEL_RU: dict[WarmingPhase, str] = {
    "intro": "🥚 Новый · 0-3 дн",
    "settling": "🐣 Адаптация · 3-7 дн",
    "warming": "🐥 Развитие · 8-14 дн",
    "active": "🐤 Окрепший · 15-29 дн",
    "warmed": "🦅 Зрелый · 30+ дн",
}


def _warming_days_since(
    record: WarmingStateRecord | None,
    now: datetime,
) -> int | None:
    """Whole days since ``started_at`` was first stamped on the warming record.

    ``None`` when warming has never run on this account, so the card can hide
    the "в прогреве N дн" hint instead of showing a misleading zero.
    """
    if record is None or not record.started_at:
        return None
    try:
        started = datetime.fromisoformat(record.started_at)
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    delta_hours = (now - started).total_seconds() / 3600.0
    return max(0, int(delta_hours // _HOURS_PER_DAY))


def _to_card(
    account: AccountRead,
    record: WarmingStateRecord | None,
    *,
    readiness: WarmingReadiness | None = None,
) -> WarmingAccountState:
    state: WarmingState = record.state if record else "idle"
    return WarmingAccountState(
        account_id=account.account_id,
        label=account.label or account.account_id,
        state=state,
        health=warming_health(state),
        cycles_completed=record.cycles_completed if record else 0,
        last_event=record.last_event if record else None,
        last_cycle_at=record.last_cycle_at if record else None,
        next_run_at=record.next_run_at if record else None,
        updated_at=record.updated_at if record else None,
        last_error=record.last_error if record else None,
        last_action=record.last_action if record else None,
        last_channel=record.last_channel if record else None,
        heartbeat_at=record.heartbeat_at if record else None,
        started_at=record.started_at if record else None,
        stopped_at=record.stopped_at if record else None,
        flood_wait_seconds=record.flood_wait_seconds if record else None,
        flood_wait_until=record.flood_wait_until if record else None,
        proxy_snapshot=record.proxy_snapshot if record else None,
        daily_actions=record.daily_actions if record else 0,
        daily_count_date=record.daily_count_date if record else None,
        quarantine_count=record.quarantine_count if record else 0,
        readiness=readiness,
    )


async def load_board() -> WarmingBoardState:
    accounts = await list_accounts()
    records = {record.account_id: record for record in await list_warming_states()}
    channels = await list_warming_channels()
    masked = await load_settings()
    # Bulk-load the per-account health signals once (was an N+1: every card
    # re-fetched account/state/spam/fingerprint via account_trust_score).
    spam_by_account = await list_spam_statuses()
    fingerprints = await list_device_fingerprints()
    channel_count = len(channels.channels)
    now = datetime.now(UTC)
    idle: list[WarmingAccountState] = []
    warming: list[WarmingAccountState] = []
    for account in accounts.accounts:
        record = records.get(account.account_id)
        spam = spam_by_account.get(account.account_id)
        fingerprint = fingerprints.get(account.account_id)
        lang_code = fingerprint.system_lang_code if fingerprint else None

        trust = account_trust_score_from(
            account=account,
            record=record,
            spam=spam,
            lang_code=lang_code,
            now=now,
        )
        readiness = evaluate_readiness(account, channel_count, spam=spam, trust_score=trust)
        card = _to_card(account, record, readiness=readiness)

        age_hours = _account_age_hours(account, now)
        intensity = compute_intensity(age_hours, trust_band=trust.band)
        card.trust_score = trust.score
        card.trust_band = trust.band
        card.trust_reasons = trust.reasons
        if spam is not None:
            card.spam_status = spam.status
            card.spam_detail = spam.detail
        card.age_hours = age_hours
        # П11: the card shows DM allowed only when age + trust band AND current
        # readiness all permit it — matching what the engine will actually do.
        card.dm_allowed = intensity.dm_allowed and readiness.ready
        card.phone_country = country_for_phone(account.phone)
        card.proxy_country = (account.proxy_country_code or "").upper() or None
        card.phase = intensity.phase
        card.phase_label = _PHASE_LABEL_RU[intensity.phase]
        card.daily_cap = intensity.daily_cap
        card.progress_to_next = intensity.progress_to_next
        card.days_to_next_phase = intensity.days_to_next_phase
        card.warming_days = _warming_days_since(record, now)
        (warming if is_warming(card.state) else idle).append(card)
    return WarmingBoardState(
        idle=idle,
        warming=warming,
        channels=channels,
        settings=masked,
        channel_count=len(channels.channels),
        active_count=sum(1 for card in warming if card.state == "active"),
        summary=_build_summary([*idle, *warming]),
    )


_TRUST_HEALTHY_BANDS: Final = frozenset({"excellent", "good"})
_TRUST_RISK_BANDS: Final = frozenset({"at_risk", "critical"})
_ATTENTION_STATES: Final = frozenset({"flood_wait", "quarantine", "error"})


def _build_summary(cards: list[WarmingAccountState]) -> WarmingSummary:
    return WarmingSummary(
        total=len(cards),
        warming=sum(1 for card in cards if is_warming(card.state)),
        active=sum(1 for card in cards if card.state == "active"),
        ready=sum(
            1
            for card in cards
            if not is_warming(card.state) and card.readiness is not None and card.readiness.ready
        ),
        attention=sum(1 for card in cards if card.state in _ATTENTION_STATES),
        trust_healthy=sum(1 for card in cards if card.trust_band in _TRUST_HEALTHY_BANDS),
        trust_watch=sum(1 for card in cards if card.trust_band == "watch"),
        trust_risk=sum(1 for card in cards if card.trust_band in _TRUST_RISK_BANDS),
    )
