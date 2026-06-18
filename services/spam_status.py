"""Account spam-status service: parse the @SpamBot probe and cache the verdict.

Pure parsing logic plus a TTL cache around the gateway probe. No Telethon, no
SQLAlchemy — those live in ``core/*``. Callers (pre-flight, quarantine recovery)
use :func:`refresh_spam_status`.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime

from core.config import settings
from core.db import get_spam_status, upsert_spam_status
from core.logging import log_event
from core.telegram_client import check_spam_status
from schemas.spam_status import SpamStatusKind, SpamStatusProbe, SpamStatusVerdict

_SECONDS_PER_HOUR = 3600

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
    # RU — long form: "Хорошие новости, ... не применены никакие ограничения."
    "хорошие новости",
    "не применены",
    # RU — short form: "Ваш аккаунт свободен от каких-либо ограничений."
    # Has to be matched explicitly because the substring "ограничен" appears in
    # both the clean ("ограничений") and limited ("наложены ограничения") forms
    # and is therefore not a safe limited-marker.
    "свободен от",
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
    # RU — Russian limited reply consistently contains the "наложены" stem.
    # We deliberately do NOT match the "ограничен" stem here: it also appears
    # in the Russian *clean* reply ("свободен от каких-либо ограничений") and
    # would cause a false-positive limited verdict. "Наложены" is specific to
    # limited replies and is the only safe Russian discriminator.
    "наложены",
)
# Latin stem covers en/de/fr/it/pt/es ("automated", "automatisch", "automatique",
# "automatizzato", "automatizado"); Cyrillic stem covers ru. Anything else with
# this stem in @SpamBot's reply means automated review is in progress.
_AUTOMATED_MARKERS = ("automat", "автомат")

# Date patterns — language-agnostic signal that the reply describes a
# time-bounded limitation ("until <date>"). @SpamBot clean replies never carry
# dates on any locale we've observed, so finding one almost certainly means
# the account is currently limited.
_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Numeric: 12.07.2026 / 12/07/2026 / 12-07-26 / 12.7.2026
    re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b"),
    # ISO: 2026-07-12
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b"),
    # Day + month-name + 4-digit year: "12 July 2026", "12. Juli 2026",
    # "12 июля 2026", "12 de julio de 2026". Month-name as any 3-9 letter
    # word; the 4-digit year constraint keeps the regex from grabbing
    # arbitrary "<num> <word> <num>" sequences.
    re.compile(r"\b\d{1,2}\.?\s+(?:de\s+)?\w{3,9}\.?\s+(?:de\s+)?\d{4}\b", re.UNICODE),
)


def _find_date(text: str) -> str | None:
    """Return the first date-like substring in ``text``, or ``None``."""
    for pat in _DATE_PATTERNS:
        match = pat.search(text)
        if match:
            return match.group(0)
    return None


def _extract_until(text: str) -> str | None:
    """Pull the "until <date>" tail of a limited reply, falling back to any date.

    The English "until" prefix is the cleanest detail when present; for other
    locales we still surface the date itself so the operator sees when the
    limitation expires.
    """
    idx = text.lower().find("until")
    if idx != -1:
        tail = text[idx:].splitlines()[0].strip().rstrip(".")
        if tail:
            return tail
    return _find_date(text)


def _classify_reply_text(text: str) -> tuple[SpamStatusKind, str | None] | None:
    """Match the reply against the explicit keyword markers (en/ru/de).

    ``None`` = no marker matched; caller falls back to the heuristic.
    """
    lowered = text.lower()
    if any(marker in lowered for marker in _CLEAN_MARKERS):
        return "clean", None
    if any(marker in lowered for marker in _BEING_CHECKED_MARKERS):
        return "unknown", "account is being checked"
    if any(marker in lowered for marker in _LIMITED_MARKERS):
        return "limited", _extract_until(text)
    return None


def _classify_by_heuristic(text: str) -> tuple[SpamStatusKind, str | None]:
    """Language-agnostic verdict for a non-empty reply when no marker matched.

    Stable invariants of @SpamBot's replies, observed across en/ru/de and
    documented in Telegram's spam FAQ: clean messages never carry dates;
    time-bounded limits always do; automated review wording always contains
    the ``automat`` / ``автомат`` stem. So a reply with a date is limited, a
    reply mentioning automation is being-checked, and anything else short of
    those is treated as clean — better than failing closed on locales we
    don't have explicit keywords for.
    """
    date = _find_date(text)
    if date is not None:
        return "limited", _extract_until(text) or date
    lowered = text.lower()
    if any(marker in lowered for marker in _AUTOMATED_MARKERS):
        return "unknown", "account is being checked"
    return "clean", None


def classify_spam_probe(probe: SpamStatusProbe) -> tuple[SpamStatusKind, str | None]:
    """Map a raw probe to a (status, detail) verdict.

    Pipeline:
    1. Probe error → unknown with the exception string in ``detail``.
    2. Explicit keyword markers (en/ru/de) → precise verdict if any match.
    3. ``getFullUser.restricted`` flag → limited (Telegram-side hard restriction).
    4. Non-empty reply with no marker match → language-agnostic heuristic
       (date → limited, ``automat`` stem → being-checked, otherwise clean).
    5. Empty / no reply → unknown with no detail.
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
        return _classify_by_heuristic(text)
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
