"""Account spam-status service: parse the @SpamBot probe and cache the verdict.

Pure parsing logic plus a TTL cache around the gateway probe. No Telethon, no
SQLAlchemy — those live in ``core/*``. Callers (pre-flight, quarantine recovery)
use :func:`refresh_spam_status`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.config import settings
from core.db import get_spam_status, upsert_spam_status
from core.logging import log_event
from core.telegram_client import check_spam_status
from schemas.spam_status import SpamStatusKind, SpamStatusProbe, SpamStatusVerdict

_SECONDS_PER_HOUR = 3600


def _extract_until(text: str) -> str | None:
    """Pull the "until <date>" tail out of a limited-account reply, if present."""
    idx = text.lower().find("until")
    if idx == -1:
        return None
    tail = text[idx:].splitlines()[0].strip().rstrip(".")
    return tail or None


def classify_spam_probe(probe: SpamStatusProbe) -> tuple[SpamStatusKind, str | None]:
    """Map a raw probe to a (status, detail) verdict by signal words.

    Parses by resilient signal words rather than exact strings, because the bot
    wording changes over time. ``getFullUser`` restriction flags are a secondary
    signal — they cover terms/country restrictions, not spam limits.
    """
    if probe.error:
        return "unknown", probe.error
    lowered = (probe.reply_text or "").lower()
    if "no limits" in lowered or "good news" in lowered:
        return "clean", None
    if "being checked" in lowered or "is checked" in lowered:
        return "unknown", "account is being checked"
    if "limited" in lowered or "restricted" in lowered:
        return "limited", _extract_until(probe.reply_text or "")
    if probe.restricted:
        return "limited", probe.restriction_reason
    return "unknown", None


def _is_fresh(checked_at: str, now: datetime) -> bool:
    ttl_hours = settings.warming.spam_status_ttl_hours
    if ttl_hours <= 0:
        return False
    try:
        checked = datetime.fromisoformat(checked_at)
    except ValueError:
        return False
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=UTC)
    return (now - checked).total_seconds() < ttl_hours * _SECONDS_PER_HOUR


async def refresh_spam_status(account_id: str, *, force: bool = False) -> SpamStatusVerdict:
    """Return the account's spam-status verdict, re-probing @SpamBot if stale.

    A fresh cached verdict within ``spam_status_ttl_hours`` is reused (probing
    @SpamBot too often is itself suspicious). ``force`` bypasses the cache.
    """
    now = datetime.now(UTC)
    cached = await get_spam_status(account_id)
    if not force and cached is not None and _is_fresh(cached.checked_at, now):
        return cached

    probe = await check_spam_status(account_id)
    status, detail = classify_spam_probe(probe)
    saved = await upsert_spam_status(
        SpamStatusVerdict(
            account_id=account_id,
            status=status,
            detail=detail,
            checked_at=now.isoformat(),
        ),
    )
    await log_event(
        "INFO" if status != "limited" else "WARNING",
        "spam_status_refreshed",
        account_id=account_id,
        extra={"status": status, "detail": detail},
    )
    return saved
