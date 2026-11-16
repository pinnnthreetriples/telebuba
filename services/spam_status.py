"""The @SpamBot probe executor: ask, classify, cache the verdict, journal it.

Two entry points. :func:`probe_and_store` always asks and is what the gateway
(``services.spam_intel``) calls once its budget has said yes. :func:`refresh_spam_status`
is the pre-gateway function — a TTL cache around the probe with a per-account lock — kept
as the executor the gateway falls back to when ``SPAM_INTEL__ENABLED=false`` and as the name
the old tests exercise. Callers outside this package and ``services.spam_intel`` must not
use either: ``tests/test_architecture.py`` enforces the gateway as the only way in.

No Telethon, no SQLAlchemy — those live in ``core/*``. Parsing is in
``services._spam_parse``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Literal

from core.config import settings
from core.db import fetch_account, get_spam_status, upsert_spam_status
from core.logging import log_event
from core.repositories.device_fingerprint import list_device_fingerprints_by_ids
from core.repositories.spam_reply_samples import upsert_reply_sample
from core.telegram_client import check_spam_status
from schemas.spam_intel import ReplySample
from schemas.spam_status import (
    ProbeOutcome,
    SpamStatusKind,
    SpamStatusProbe,
    SpamStatusVerdict,
)
from services._spam_parse import (
    classify_legacy,
    classify_strict,
    find_date,
    redact_reply,
    reply_hash,
)
from services.lifecycle_journal import record_lifecycle_event
from services.spam_block_lift import lift_on_clean

logger = logging.getLogger(__name__)

_SECONDS_PER_HOUR = 3600


def classify_spam_probe(probe: SpamStatusProbe) -> tuple[SpamStatusKind, str | None]:
    """Map a raw probe to a (status, detail) verdict.

    The strict classifier when ``SPAM_INTEL__STRICT_PARSER`` is on (a reply nothing
    recognised is ``unknown``), the pre-gateway one otherwise.
    """
    if settings.spam_intel.strict_parser:
        return classify_strict(probe)
    return classify_legacy(probe)


def is_reading_fresh(checked_at: str, now: datetime) -> bool:
    """Whether a stored reading is still inside ``spam_status_ttl_hours``."""
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


async def _observe_reply(account_id: str, probe: SpamStatusProbe, now: datetime) -> None:
    """Shadow-compare the two classifiers and keep a redacted sample; never fails the probe."""
    if probe.error or not (probe.reply_text or "").strip():
        return
    intel = settings.spam_intel
    legacy, strict = classify_legacy(probe), classify_strict(probe)
    try:
        if not intel.strict_parser and intel.parser_shadow and legacy[0] != strict[0]:
            await log_event(
                "WARNING",
                "spam_intel_parser_shadow_disagreement",
                account_id=account_id,
                extra={"legacy": legacy[0], "strict": strict[0], "strict_detail": strict[1]},
            )
        if intel.sample_capture:
            await store_reply_sample(account_id, probe.reply_text or "", "probe", now)
    except Exception as exc:  # noqa: BLE001 - observation must never fail a probe.
        logger.warning("spam reply observation failed: %s", type(exc).__name__, exc_info=exc)


async def store_reply_sample(
    account_id: str,
    text: str,
    source: Literal["probe", "history"],
    now: datetime,
) -> None:
    """Keep one reply of @SpamBot, redacted, with what each classifier makes of it."""
    account = await fetch_account(account_id)
    names = (
        [account.first_name, account.last_name, account.username, account.phone]
        if account is not None
        else []
    )
    redacted = redact_reply(text, names, settings.spam_intel.sample_max_chars)
    if not redacted:
        return
    probe = SpamStatusProbe(account_id=account_id, reply_text=text)
    fingerprint = (await list_device_fingerprints_by_ids([account_id])).get(account_id)
    stamp = now.isoformat()
    await upsert_reply_sample(
        ReplySample(
            text_hash=reply_hash(redacted),
            sample=redacted,
            lang=fingerprint.lang_code if fingerprint is not None else None,
            source=source,
            legacy_status=classify_legacy(probe)[0],
            strict_status=classify_strict(probe)[0],
            has_date=find_date(redacted) is not None,
            first_seen_at=stamp,
            last_seen_at=stamp,
        ),
        settings.spam_intel.samples_max_rows,
    )


async def probe_and_store(
    account_id: str,
    *,
    previous: SpamStatusVerdict | None = None,
) -> ProbeOutcome:
    """Ask @SpamBot now, classify, persist, journal, lift — no cache check, no budget.

    A probe that never reached the bot returns an ``unknown`` for the caller only, with
    ``error`` set: caching it would serve it as fresh for ``spam_status_ttl_hours`` (36 h by
    default), long past the point where a real reading could be had. Warming's quarantine
    gate requires a positive ``clean`` to release, so an ``unknown`` holds the quarantine
    rather than opening it.
    """
    now = datetime.now(UTC)
    probe = await check_spam_status(account_id)
    status, detail = classify_spam_probe(probe)
    await _observe_reply(account_id, probe, now)
    if probe.error:
        # No second log line: the gateway already recorded the refusal under
        # ``telegram_spam_status_probe_failed`` (bounded to the exception class name — the
        # full text is on its stdlib logger, not in ``extra``).
        return ProbeOutcome(verdict=_uncached_unknown(account_id, detail, now), error=probe.error)
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
        # ``previous``: the stored answer this one replaces, so a reader can tell a new
        # restriction from a known one (the bot pings only on news).
        extra={
            "status": status,
            "detail": detail,
            "previous": previous.status if previous is not None else None,
        },
    )
    await record_lifecycle_event(
        account_id, "spambot_verdict", {"status": status, "detail": detail}
    )
    # A clean answer ends a confirmed spam block, whoever asked (``spam_block_lift``).
    await lift_on_clean(account_id, saved)
    return ProbeOutcome(verdict=saved)


async def refresh_spam_status(account_id: str, *, force: bool = False) -> SpamStatusVerdict:
    """The pre-gateway funnel: a fresh cached verdict is reused, else probe @SpamBot.

    ``force`` bypasses the cache. A per-account asyncio lock makes concurrent
    cache-respecting callers wait for the first probe and then read its cached verdict. A
    FORCED caller that waited on the lock probes again regardless; the gateway's
    single-flight is what coalesces those.
    """
    async with _refresh_lock(account_id):
        now = datetime.now(UTC)
        if await fetch_account(account_id) is None:
            # Guard, don't probe: with no row there is no stored session name, so
            # ``_session_path`` falls back to ``session_dir / account_id`` and Telethon's
            # SQLiteSession CREATES that file on construction. A spam-check must never mint
            # a credential for an account that does not exist.
            return _uncached_unknown(account_id, "account not found", now)
        cached = await get_spam_status(account_id)
        if not force and cached is not None and is_reading_fresh(cached.checked_at, now):
            return cached
        return (await probe_and_store(account_id, previous=cached)).verdict


def _uncached_unknown(account_id: str, detail: str | None, now: datetime) -> SpamStatusVerdict:
    """An ``unknown`` verdict for the caller only — deliberately never persisted."""
    return SpamStatusVerdict(
        account_id=account_id,
        status="unknown",
        detail=detail,
        checked_at=now.isoformat(),
    )


_REFRESH_LOCKS: dict[str, asyncio.Lock] = {}


def _refresh_lock(account_id: str) -> asyncio.Lock:
    lock = _REFRESH_LOCKS.get(account_id)
    if lock is None:
        lock = asyncio.Lock()
        _REFRESH_LOCKS[account_id] = lock
    return lock
