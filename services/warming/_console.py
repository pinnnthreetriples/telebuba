"""The operator-console rows of a warming card (Прогрев 2.0, stage 2).

Pure functions over what ``board._load_cards`` already bulk-loaded: today's settled
actions, why DMs are (not) open, the one problem worth the operator's attention, and
the phone/proxy geo verdict with the operator's consent to it. Codes only — the SPA
owns every word.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.config import settings
from core.phone_geo import evaluate_geo
from schemas._warming_console import WarmingAttention
from services.warming._real_age import dm_open_after_hours

if TYPE_CHECKING:
    from schemas._warming_console import DmStatus
    from schemas.accounts import AccountRead
    from schemas.geo import GeoMatch
    from schemas.warming import (
        WarmingIntensity,
        WarmingReadiness,
        WarmingSettings,
        WarmingStateRecord,
    )

_SECONDS_PER_HOUR = 3600
# Errors that only mean "the previous loop is still unwinding".
_STOPPING_EVENTS = frozenset({"restart_stopping", "stop_stopping", "stop_timeout"})


def account_geo(account: AccountRead) -> GeoMatch:
    """Phone country against the assigned proxy's country (``proxies.country_code``)."""
    return evaluate_geo(phone=account.phone, proxy_country=account.proxy_country_code)


def geo_pair(geo: GeoMatch) -> str | None:
    """The consent key ``PHONE>PROXY`` of a mismatch, ``None`` when nothing differs."""
    if geo.status != "mismatch":
        return None
    return f"{geo.phone_country}>{geo.proxy_country}"


def geo_accepted(geo: GeoMatch, record: WarmingStateRecord | None) -> bool:
    """True while the stored consent names this very pair; a new proxy country voids it."""
    pair = geo_pair(geo)
    return pair is not None and record is not None and record.geo_ack == pair


def settled_today(record: WarmingStateRecord | None) -> int:
    """Today's actions actually spent, not the budget a running cycle has booked.

    While a cycle runs the row carries its whole reservation (``daily_actions``), so the
    card would read "15/15" mid-cycle; the count settled before that booking is the
    honest number until the cycle reconciles.
    """
    if record is None:
        return 0
    in_flight = record.state == "active" and record.last_event == "cycle_started"
    if in_flight and record.cycle_base_actions is not None:
        return min(record.cycle_base_actions, record.daily_actions)
    return record.daily_actions


def _warming_hours(started_at: str | None, now: datetime) -> float:
    if started_at is None:
        return 0.0
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return 0.0
    started = started if started.tzinfo is not None else started.replace(tzinfo=UTC)
    return (now - started).total_seconds() / _SECONDS_PER_HOUR


def dm_status(  # noqa: PLR0911, PLR0913 - one return per gate; the gates read six inputs.
    masked: WarmingSettings,
    intensity: WarmingIntensity,
    readiness: WarmingReadiness,
    started_at: str | None,
    now: datetime,
    registered_at: str | None = None,
) -> DmStatus:
    """Why this account's inter-account DMs are open or closed, first blocker first.

    Mirrors the engine's gates (``_chat._should_chat`` + ``compute_intensity``): the
    switch, a text-LLM key, the fleet pause, readiness (only when enforced), then the
    account's own warming time (a day shorter for a half-year-old registration) and
    its trust band or post-quarantine DM block.
    """
    if not masked.inter_account_chat:
        return "chat_off"
    if not (masked.has_gemini_key or masked.has_deepseek_key):
        return "no_llm_key"
    if masked.dm_paused_until is not None:
        return "paused"
    if masked.enforce_readiness and not readiness.ready:
        return "not_ready"
    if intensity.dm_allowed:
        return "on"
    if _warming_hours(started_at, now) < dm_open_after_hours(registered_at, now):
        return "too_young"
    return "trust"


def account_dm_status(fleet: DmStatus, *, spam_blocked: bool) -> DmStatus:
    """The fleet-level verdict, unless the account's own confirmed spam block shuts DMs.

    Ranks after the two switches that shut DMs for everyone (chat off, no LLM key) and
    before everything else: the block is permanent, so no pause or trust band matters.
    """
    if spam_blocked and fleet not in {"chat_off", "no_llm_key"}:
        return "spam_blocked"
    return fleet


def attention(
    record: WarmingStateRecord | None,
    readiness: WarmingReadiness,
) -> WarmingAttention | None:
    """The single most urgent problem on a card, or ``None`` when nothing needs a look."""
    if record is not None and record.state == "spam_blocked":
        # Warming stopped and waits for the operator: delete the account or keep it.
        return WarmingAttention(code="spam_blocked")
    if record is not None and record.state == "quarantine":
        return WarmingAttention(
            code="quarantine",
            until=record.next_run_at,
            attempt=record.quarantine_count + 1,
            attempts=settings.warming.quarantine_max_repeats,
        )
    if record is not None and record.state == "flood_wait":
        return WarmingAttention(
            code="flood_wait",
            until=record.flood_wait_until or record.next_run_at,
        )
    error = _error_attention(record) if record is not None and record.state == "error" else None
    if error is not None and error.code != "error":
        return error
    if not readiness.ready:
        return WarmingAttention(code="not_ready", reasons=readiness.reasons)
    return error


def _error_attention(record: WarmingStateRecord) -> WarmingAttention:
    """Name an ``error`` row by what stopped it; a plain ``error`` ranks below readiness."""
    if record.last_event in _STOPPING_EVENTS:
        return WarmingAttention(code="stopping")
    if record.last_event == "quarantine_unreadable":
        return WarmingAttention(code="quarantine_unreadable")
    return WarmingAttention(code="error")
