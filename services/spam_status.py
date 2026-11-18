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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from core.config import settings
from core.db import fetch_account, get_spam_status, upsert_spam_status
from core.logging import log_event
from core.repositories.device_fingerprint import list_device_fingerprints_by_ids
from core.repositories.spam_reply_samples import prune_reply_samples, upsert_reply_sample
from core.telegram_client import check_spam_status, converse_spam_status
from schemas.spam_intel import ReplySample, SampleStructure
from schemas.spam_status import (
    ProbeOutcome,
    SpamStatusKind,
    SpamStatusProbe,
    SpamStatusVerdict,
)
from services._spam_parse import (
    classify_legacy,
    classify_strict,
    classify_transcript,
    find_date,
)
from services._spam_redact import redact_reply, reply_hash
from services.lifecycle_journal import record_lifecycle_event
from services.spam_block_lift import lift_on_clean

if TYPE_CHECKING:
    from schemas.spam_probe import HumanProbePlan

logger = logging.getLogger(__name__)

_SECONDS_PER_HOUR = 3600
_Reading = tuple[SpamStatusKind, str | None]


def classify_spam_probe(probe: SpamStatusProbe) -> tuple[SpamStatusKind, str | None]:
    """Map a raw probe to a (status, detail) verdict.

    The strict classifier when ``SPAM_INTEL__STRICT_PARSER`` is on (a reply nothing
    recognised is ``unknown``), the pre-gateway one otherwise. A probe that came from the
    human-like conversation (``n_messages`` > 0) is read as a transcript: every message, any
    limited one decides (``_spam_parse.classify_transcript``).
    """
    strict = settings.spam_intel.strict_parser
    if probe.n_messages:
        return classify_transcript(probe.texts, strict=strict)
    return classify_strict(probe) if strict else classify_legacy(probe)


def _compare(probe: SpamStatusProbe) -> tuple[_Reading, _Reading]:
    """What the legacy and the strict classifier each make of this probe."""
    if probe.n_messages:
        return (
            classify_transcript(probe.texts, strict=False),
            classify_transcript(probe.texts, strict=True),
        )
    return classify_legacy(probe), classify_strict(probe)


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
    """Shadow-compare the two classifiers and keep redacted samples; never fails the probe."""
    texts = probe.texts if probe.n_messages else [probe.reply_text or ""]
    texts = [text for text in texts if text.strip()]
    if probe.error or not texts:
        return
    intel = settings.spam_intel
    legacy, strict = _compare(probe)
    try:
        if not intel.strict_parser and intel.parser_shadow and legacy[0] != strict[0]:
            await log_event(
                "WARNING",
                "spam_intel_parser_shadow_disagreement",
                account_id=account_id,
                extra={"legacy": legacy[0], "strict": strict[0], "strict_detail": strict[1]},
            )
        if not intel.sample_capture:
            await _prune_aged_samples(now)  # switching capture off still ages out what was kept
        else:
            for text in texts:
                await store_reply_sample(
                    account_id,
                    text,
                    "probe",
                    now,
                    SampleStructure(
                        multi_message=probe.n_messages > 1, has_markup=probe.has_markup
                    ),
                )
    except Exception as exc:  # noqa: BLE001 - observation must never fail a probe.
        # The class name only: an exception's text and traceback can quote the reply or a path.
        logger.warning("spam reply observation failed: %s", type(exc).__name__)


async def _prune_aged_samples(now: datetime) -> None:
    """Drop the samples last seen more than ``SPAM_INTEL__SAMPLE_MAX_AGE_DAYS`` ago."""
    cutoff = now - timedelta(days=settings.spam_intel.sample_max_age_days)
    await prune_reply_samples(cutoff.isoformat())


async def store_reply_sample(
    account_id: str,
    text: str,
    source: Literal["probe", "history"],
    now: datetime,
    structure: SampleStructure | None = None,
) -> None:
    """Keep one reply of @SpamBot, redacted, with what each classifier makes of it.

    Only the redacted text is stored (``services._spam_redact``: names of the account, phone
    numbers in any format, ``@usernames``, links, long digit runs), at most
    ``SPAM_INTEL__SAMPLE_MAX_CHARS`` of it, and a row older than ``SAMPLE_MAX_AGE_DAYS`` is
    dropped on every write. The classification is read from the original text; ``has_date`` too.

    The ``SAMPLE_CAPTURE`` switch decides whether a PROBE keeps its replies (``_observe_reply``
    asks it; off, nothing is stored and old rows still age out). This function does not ask: the
    history sweep (``SPAM_INTEL__HISTORY_SWEEP``, the owner's explicit opt-in to read the bot's
    old replies) exists to capture them, so it stores whatever ``SAMPLE_CAPTURE`` says. Either
    way what lands in the table is redacted.

    ``structure``: the flags of the conversation the text came from (the bot sent more than one
    message; a message carried buttons). A history read has none.
    """
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
    await _prune_aged_samples(now)
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
            has_date=find_date(text) is not None,
            multi_message=structure.multi_message if structure else False,
            has_markup=structure.has_markup if structure else False,
            first_seen_at=stamp,
            last_seen_at=stamp,
        ),
        settings.spam_intel.samples_max_rows,
    )


async def probe_and_store(
    account_id: str,
    *,
    previous: SpamStatusVerdict | None = None,
    plan: HumanProbePlan | None = None,
) -> ProbeOutcome:
    """Ask @SpamBot now, classify, persist, journal, lift — no cache check, no budget.

    ``plan``: the pauses of the human-like conversation (the gateway draws one when
    ``SPAM_INTEL__HUMAN_SEQUENCE`` is on). Without it the single ``/start`` probe asks, as
    before.

    A probe that never reached the bot returns an ``unknown`` for the caller only, with
    ``error`` set: caching it would serve it as fresh for ``spam_status_ttl_hours`` (36 h by
    default), long past the point where a real reading could be had. Warming's quarantine
    gate requires a positive ``clean`` to release, so an ``unknown`` holds the quarantine
    rather than opening it.
    """
    now = datetime.now(UTC)
    probe = (
        await check_spam_status(account_id)
        if plan is None
        else await converse_spam_status(account_id, plan)
    )
    status, detail = classify_spam_probe(probe)
    await _observe_reply(account_id, probe, now)
    if probe.error:
        # No second log line: the gateway already recorded the refusal under
        # ``telegram_spam_status_probe_failed`` (bounded to the exception class name — the
        # full text is on its stdlib logger, not in ``extra``).
        return ProbeOutcome(verdict=_uncached_unknown(account_id, detail, now), error=probe.error)
    # The reading this one replaces stays evidence: a reply without a verdict ("being checked")
    # must not take a still-valid dated limit with it (audit F-015).
    await _keep_prior_reading(account_id)
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
            # Its date, so a notification can tell a limit that was still on from one that had
            # already ended (a new limit after an ended one is news, not a repeat).
            "previous_detail": previous.detail if previous is not None else None,
        },
    )
    await record_lifecycle_event(
        account_id, "spambot_verdict", {"status": status, "detail": detail}
    )
    # A clean answer ends a confirmed spam block, whoever asked (``spam_block_lift``).
    await lift_on_clean(account_id, saved)
    return ProbeOutcome(verdict=saved)


async def _keep_prior_reading(account_id: str) -> None:
    """Record the stored reading as evidence before it is replaced (``spam_intel._evidence``)."""
    # Imported here: the evidence layer sits in ``services.spam_intel``, whose package root
    # imports this module (the gateway), so a top-level import would be a cycle.
    from services.spam_intel._evidence import keep_prior_reading  # noqa: PLC0415

    await keep_prior_reading(account_id)


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
