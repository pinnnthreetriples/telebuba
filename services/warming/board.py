"""Warming kanban board read model.

Builds the board state the UI polls: one card per account with its warming
state plus bulk-loaded health signals (trust/spam/age-ramp). All DB rows are
fetched once here — there is no per-card N+1.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from core.config import settings
from core.db import (
    list_accounts,
    list_device_fingerprints,
    list_spam_statuses,
    list_warming_channels,
    list_warming_states,
)
from core.phone_geo import country_for_phone
from schemas.warming import (
    WarmedAccount,
    WarmedAccountList,
    WarmingAccountState,
    WarmingBoardState,
    WarmingChannelList,
    WarmingSettings,
    WarmingState,
    WarmingSummary,
    is_warming,
    warming_health,
)
from services.trust import account_trust_score_from
from services.warming.pacing import (
    _account_age_hours,
    compute_intensity,
    evaluate_readiness,
    warming_days_since,
)
from services.warming.settings_store import load_settings

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.warming import WarmingReadiness, WarmingStateRecord


def _warming_days_since(
    record: WarmingStateRecord | None,
    now: datetime,
) -> int | None:
    """Whole days since ``started_at`` was first stamped on the warming record.

    ``None`` when warming has never run on this account, so the card can hide
    the "в прогреве N дн" hint instead of showing a misleading zero.

    A stopped/promoted account's count is frozen at ``stopped_at`` (the record's
    ``state``/``stopped_at`` are forwarded) so the warmed card's "X/Y дней" stops
    climbing past Y with wall-clock time.
    """
    if record is None:
        return warming_days_since(None, now)
    return warming_days_since(
        record.started_at,
        now,
        stopped_at=record.stopped_at,
        state=record.state,
    )


def _to_card(
    account: AccountRead,
    record: WarmingStateRecord | None,
    *,
    readiness: WarmingReadiness | None = None,
) -> WarmingAccountState:
    state: WarmingState = record.state if record else "idle"
    # Collapse the per-field `record.x if record else default` ternaries (one per
    # column, ~20 branches) into a single dict-merge: an empty record produces an
    # empty dict, the schema defaults fill in. Keeps cyclomatic complexity at 2.
    # Drop record-only fields that have no card counterpart (run_id / current_phase /
    # phase_entered_at are warming-loop internals, not card affordances).
    record_fields = (
        record.model_dump(
            exclude={"account_id", "state", "run_id", "current_phase", "phase_entered_at"}
        )
        if record
        else {}
    )
    return WarmingAccountState(
        account_id=account.account_id,
        label=account.label or account.account_id,
        state=state,
        health=warming_health(state),
        readiness=readiness,
        **record_fields,
    )


async def _load_cards() -> tuple[list[WarmingAccountState], WarmingChannelList, WarmingSettings]:
    """Build one enriched card per account, unfiltered.

    Shared by ``load_board`` (which splits the result into idle/warming) and
    ``list_warmed_accounts`` (which needs promoted accounts that ``load_board``
    deliberately excludes from both those buckets) — avoids re-deriving the
    same trust/readiness/phase enrichment in two places.
    """
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
    cards: list[WarmingAccountState] = []
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
        # П11: mirror the engine — DM needs age + trust band, plus readiness only
        # when enforce_readiness is on. With it off the engine skips the readiness
        # gate, so the card must not show DM blocked while the engine still sends.
        card.dm_allowed = intensity.dm_allowed and (readiness.ready or not masked.enforce_readiness)
        card.phone_country = country_for_phone(account.phone)
        card.proxy_country = (account.proxy_country_code or "").upper() or None
        card.phone = account.phone
        card.proxy_type = account.proxy_type
        card.phase = intensity.phase
        card.daily_cap = intensity.daily_cap
        card.progress_to_next = intensity.progress_to_next
        card.days_to_next_phase = intensity.days_to_next_phase
        card.warming_days = _warming_days_since(record, now)
        cards.append(card)
    return cards, channels, masked


def _is_warmed(card: WarmingAccountState, min_days: int) -> bool:
    """Belongs in the neurocomment "warmed" pool: promoted AND past the day floor.

    A promoted account *below* the floor is deliberately kept out of that pool
    (``list_warmed_accounts``). It must therefore fall back to the idle "ready to
    warm" column rather than vanish from both — otherwise a graduation before the
    floor strands the account in no column at all.
    """
    return card.promoted_to_nc and card.warming_days is not None and card.warming_days >= min_days


async def load_board() -> WarmingBoardState:
    cards, channels, masked = await _load_cards()
    # An account that has fully graduated (promoted AND past the warmed-day floor)
    # lives in the neurocomment warmed pool, not "ready to warm". A promotion made
    # before the floor stays recoverable here in idle (see _is_warmed).
    min_days = settings.neurocomment.warmed_min_days
    idle = [card for card in cards if not is_warming(card.state) and not _is_warmed(card, min_days)]
    warming = [card for card in cards if is_warming(card.state)]
    return WarmingBoardState(
        idle=idle,
        warming=warming,
        channels=channels,
        settings=masked,
        channel_count=len(channels.channels),
        active_count=sum(1 for card in warming if card.state == "active"),
        summary=_build_summary([*idle, *warming]),
        card_log_limit=settings.warming.card_log_limit,
    )


async def list_warmed_accounts(min_days: int) -> WarmedAccountList:
    """Accounts the operator has graduated into the neurocomment pool.

    Promotion is explicit: the warming card carries a «переместить в нейрокомментинг»
    button that flips ``promoted_to_nc``. An account that has merely crossed
    ``min_days`` of warming does NOT auto-appear here — the hand-off is a deliberate
    operator action so partially-warmed accounts can't slip into commenting. The
    ``min_days`` argument is kept as a sanity floor (so an accidental click on a
    fresh account doesn't promote it), but the primary filter is the flag.
    """
    cards, _channels, _masked = await _load_cards()
    warmed = [
        WarmedAccount(
            account_id=card.account_id,
            label=card.label,
            warming_days=days,
            phone=card.phone,
            phone_country=card.phone_country,
            proxy_type=card.proxy_type,
            trust_score=card.trust_score,
            target_days=card.target_days or min_days,
        )
        for card in cards
        if _is_warmed(card, min_days)
        # ``_is_warmed`` already guarantees this; the walrus narrows int|None → int.
        if (days := card.warming_days) is not None
    ]
    warmed.sort(key=lambda a: a.warming_days, reverse=True)
    return WarmedAccountList(accounts=warmed)


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
