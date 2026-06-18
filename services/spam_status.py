"""Account spam-status service: parse the @SpamBot probe and cache the verdict.

Pure parsing logic plus a TTL cache around the gateway probe. No Telethon, no
SQLAlchemy — those live in ``core/*``. Callers (pre-flight, quarantine recovery)
use :func:`refresh_spam_status`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from core.config import settings
from core.db import get_spam_status, upsert_spam_status
from core.logging import log_event
from core.telegram_client import check_spam_status
from schemas.spam_status import SpamStatusKind, SpamStatusProbe, SpamStatusVerdict

_SECONDS_PER_HOUR = 3600
_UNRECOGNISED_REPLY_MAX = 160

# Substring markers, lowercased, ordered by check priority. ``@SpamBot``
# localises its reply to the account's interface language, so the same
# verdict can land in EN, RU, or anything else; matching by short stems lets
# one rule cover several inflections of one language. Clean is checked first
# because the Russian clean reply ("не применены ограничения") legitimately
# contains a substring shared with the limited markers.
_CLEAN_MARKERS = (
    # EN — "Good news, no limits are currently applied to your account."
    "no limits",
    "good news",
    # RU — "Хорошие новости, к вашему аккаунту не применены никакие ограничения."
    "хорошие новости",
    "не применены",
)
_BEING_CHECKED_MARKERS = (
    # EN — "Your account is being checked."
    "being checked",
    "is checked",
    # RU — "Ваш аккаунт сейчас проверяется автоматизированной системой."
    "сейчас проверя",
    "автоматизирован",
)
_LIMITED_MARKERS = (
    # EN — "Your account is now limited until …"
    "limited",
    "restricted",
    # RU — typical limited reply contains the "ограничен" / "наложены" stems
    "ограничен",
    "наложены",
)


def _extract_until(text: str) -> str | None:
    """Pull the "until <date>" tail out of a limited-account reply, if present."""
    idx = text.lower().find("until")
    if idx == -1:
        return None
    tail = text[idx:].splitlines()[0].strip().rstrip(".")
    return tail or None


def _classify_reply_text(text: str) -> tuple[SpamStatusKind, str | None] | None:
    """Match the reply against the keyword markers. ``None`` = no match yet."""
    lowered = text.lower()
    if any(marker in lowered for marker in _CLEAN_MARKERS):
        return "clean", None
    if any(marker in lowered for marker in _BEING_CHECKED_MARKERS):
        return "unknown", "account is being checked"
    if any(marker in lowered for marker in _LIMITED_MARKERS):
        return "limited", _extract_until(text)
    return None


def _unrecognised_reply_detail(text: str) -> str:
    snippet = text.strip()
    if len(snippet) > _UNRECOGNISED_REPLY_MAX:
        snippet = snippet[: _UNRECOGNISED_REPLY_MAX - 1] + "…"
    return f"нераспознанный ответ: {snippet}"


def classify_spam_probe(probe: SpamStatusProbe) -> tuple[SpamStatusKind, str | None]:
    """Map a raw probe to a (status, detail) verdict by signal words.

    Parses by resilient signal words rather than exact strings, because the bot
    wording changes over time and varies by locale. ``getFullUser`` restriction
    flags are a secondary signal — they cover terms/country restrictions, not
    spam limits. When nothing matches, we surface the raw reply in ``detail`` so
    the operator can see what came back instead of being told "вердикт не
    получен" with no clue why.
    """
    if probe.error:
        return "unknown", probe.error
    text = probe.reply_text or ""
    matched = _classify_reply_text(text)
    if matched is not None:
        return matched
    if probe.restricted:
        return "limited", probe.restriction_reason
    if text.strip():
        return "unknown", _unrecognised_reply_detail(text)
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

    A per-account asyncio lock collapses concurrent callers: the first one
    probes, the rest wait, and on wake they see the freshly-cached verdict
    instead of all hitting @SpamBot. Without this, several warming cycles
    waking together can all decide the cache is stale and probe in parallel.
    """
    async with _refresh_lock(account_id):
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


_REFRESH_LOCKS: dict[str, asyncio.Lock] = {}


def _refresh_lock(account_id: str) -> asyncio.Lock:
    lock = _REFRESH_LOCKS.get(account_id)
    if lock is None:
        lock = asyncio.Lock()
        _REFRESH_LOCKS[account_id] = lock
    return lock
