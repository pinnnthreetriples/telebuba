"""The record of a spam-block appeal a HUMAN filed (docs/spam-intel.md section 5.12).

Hard limit, first: the program never presses an @SpamBot button, never answers one of its
questions, never passes or solves its captcha and never sends appeal text. The operator does the
whole appeal in the account's own Telegram Web window. What this module does is only keep a
record: that the signed-in operator says an appeal was filed, when, and what the verdict said
then; and close it when a persisted @SpamBot ``clean`` shows the block is gone. It imports nothing
from ``core.telegram_client`` and has no send, click or type path;
``tests/services/spam_intel/test_appeal_hard_limit.py`` pins that.

* One OPEN appeal per account. Filing while one is open returns it unchanged (who and when stay
  the first filing's), so a double click records nothing new.
* An appeal is closed by a persisted @SpamBot ``clean`` (owner decision D4: only a real answer
  says the block is gone). The gateway's store path calls :func:`close_on_clean`; the re-check
  loop's pass (:func:`close_ended_appeals`) catches up on a ``clean`` stored by a path that did
  not call it. READING never writes: the card and the routes show such an appeal as closed (the
  clean's own instant) without touching the row. Closing never lifts a block: that stays
  ``services.spam_block_lift``.
* The re-check (owner decision D15) is a time this module names (:func:`recheck_at`, one source
  for the card and the loop); ``_appeal_recheck`` asks the gateway at it, an ordinary DECISION
  ask under the budget. It is due once: a real reading since the filing's own due time ends it.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_account
from core.logging import log_event
from core.repositories.spam_appeals import (
    fetch_latest_appeal,
    fetch_open_appeal,
    file_appeal,
    list_open_appeals,
)
from core.repositories.spam_status import get_spam_status, list_spam_statuses_by_ids
from schemas.accounts import health_for_status
from schemas.spam_appeals import SpamAppeal, SpamAppealState
from services.spam_intel import _appeal_close
from services.spam_intel._appeal_close import CLOSED_BY_CLEAN, close_on_clean
from services.spam_intel._evidence import verdicts_for
from services.spam_intel._limit_date import HOLD, end_date

if TYPE_CHECKING:
    from core._config_spam_intel import SpamIntelSettings
    from schemas.spam_evidence import Verdict
    from schemas.spam_status import SpamStatusKind

# The refusal code the SPA translates: nothing to appeal on a clean (or frozen) account.
NOT_LIMITED_REFUSAL = "spam_appeal_not_limited"
_JITTER_RESOLUTION = 0xFFFFFFFF


def _stamp(moment: str) -> datetime:
    parsed = datetime.fromisoformat(moment)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _jitter(appeal: SpamAppeal, cfg: SpamIntelSettings) -> timedelta:
    """A stable per-appeal offset in ``[0, appeal_recheck_jitter_hours]``.

    Derived from the row, not drawn per scan, so the time the card shows is the time the loop
    acts on, and accounts whose limits end the same day are not all asked in the same minute.
    """
    digest = hashlib.sha256(f"{appeal.account_id}:{appeal.id}".encode()).digest()
    share = int.from_bytes(digest[:4], "big") / _JITTER_RESOLUTION
    return timedelta(hours=cfg.appeal_recheck_jitter_hours * share)


def _end_of(day: date) -> datetime:
    """The instant a limit named for ``day`` is taken to end (the day, plus the design's hold)."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC) + HOLD


def _base_due(appeal: SpamAppeal, cfg: SpamIntelSettings) -> datetime:
    """The filing's own due time: its end day plus the jitter, or hours after the filing."""
    if appeal.until_day is not None:
        return _end_of(appeal.until_day) + _jitter(appeal, cfg)
    return appeal.filed_at + timedelta(hours=cfg.appeal_recheck_hours)


def recheck_at(
    appeal: SpamAppeal, verdict: Verdict | None, cfg: SpamIntelSettings
) -> datetime | None:
    """When an open appeal is asked about again, or ``None`` when no re-check will come.

    The ONE definition the card and the loop share. The filing's due time is its end day plus a
    jitter, or ``appeal_recheck_hours`` after filing an open-ended one; while the verdict is a
    dated limit that ends LATER than that, the re-check waits for the later end. An appeal is
    due once: a real @SpamBot reading at or after the filing's due time ends it, whatever it
    said (a limit it then extended gets no second re-check from here, so none is promised).
    """
    base = _base_due(appeal, cfg)
    if (
        verdict is not None
        and verdict.last_spambot_at is not None
        and verdict.last_spambot_at >= base
    ):
        return None
    if verdict is not None and verdict.status == "limited" and verdict.valid_until is not None:
        return max(base, verdict.valid_until + _jitter(appeal, cfg))
    return base


def _state(appeal: SpamAppeal | None, account_id: str, at: datetime | None) -> SpamAppealState:
    if appeal is None:
        return SpamAppealState(account_id=account_id)
    return SpamAppealState(
        account_id=account_id,
        status="open" if appeal.closed_at is None else "closed",
        filed_at=appeal.filed_at,
        filed_by=appeal.filed_by,
        filed_verdict=appeal.verdict,
        filed_until_day=appeal.until_day,
        closed_at=appeal.closed_at,
        closed_by_kind=appeal.closed_by_kind,
        recheck_at=at,
    )


def ended_by_clean(appeal: SpamAppeal, verdict: Verdict) -> datetime | None:
    """The instant a stored @SpamBot ``clean`` newer than the filing ended an open appeal.

    Read from the verdict (a stored ``clean`` there IS a real answer: a passive ``clean`` never
    decides it, owner decision D4), so a read needs no second query and no write.
    """
    if appeal.closed_at is not None or verdict.status != "clean":
        return None
    seen = verdict.primary_observed_at
    if verdict.primary_kind != "spambot_clean" or seen is None or seen < appeal.filed_at:
        return None
    return seen


async def _catch_up(appeal: SpamAppeal) -> bool:
    """Close the appeal on a stored @SpamBot ``clean`` newer than the filing; ``True`` if it did.

    A passive ``clean`` never writes the stored status (owner decision D4), so a ``clean`` there
    IS a real answer. This covers a reading stored by a path that did not call
    :func:`close_on_clean` (the gateway switched off, a legacy caller). The WRITE path only: the
    recheck loop and :func:`close_ended_appeals` call it, never a read.
    """
    stored = await get_spam_status(appeal.account_id)
    if stored is None or stored.status != "clean" or _stamp(stored.checked_at) < appeal.filed_at:
        return False
    return await close_on_clean(appeal.account_id, _stamp(stored.checked_at), appeal_id=appeal.id)


async def close_ended_appeals() -> int:
    """Persist the close of every open appeal a stored @SpamBot ``clean`` already ended.

    The background pass (the re-check loop runs it): ONE read of the open appeals and one of
    their stored readings, then a targeted close per appeal that a clean newer than its filing
    ended. Returns how many it closed.
    """
    appeals = await list_open_appeals()
    if not appeals:
        return 0
    stored = await list_spam_statuses_by_ids([appeal.account_id for appeal in appeals])
    closed = 0
    for appeal in appeals:
        row = stored.get(appeal.account_id)
        if row is None or row.status != "clean" or _stamp(row.checked_at) < appeal.filed_at:
            continue
        if await close_on_clean(appeal.account_id, _stamp(row.checked_at), appeal_id=appeal.id):
            closed += 1
    return closed


async def open_appeal(account_id: str) -> SpamAppeal | None:
    """The account's open appeal (after catching up on a ``clean`` already stored), or ``None``.

    Writes when it catches up: the loop's call, not a read path (see :func:`appeal_state`).
    """
    appeal = await fetch_open_appeal(account_id)
    if appeal is None or await _catch_up(appeal):
        return None
    return appeal


async def appeal_state(account_id: str) -> SpamAppealState:
    """What the card shows: the newest appeal, whether it is open, and its re-check time.

    Read-only: an appeal a stored @SpamBot ``clean`` already ended reads as closed (at that
    answer's instant) and is persisted by the loop's pass.
    """
    latest = await fetch_latest_appeal(account_id)
    if latest is None:
        return _state(None, account_id, None)
    verdict = (await verdicts_for([account_id]))[account_id]
    if latest.closed_at is None and (ended := ended_by_clean(latest, verdict)) is not None:
        latest = latest.model_copy(update={"closed_at": ended, "closed_by_kind": CLOSED_BY_CLEAN})
    if latest.closed_at is not None:
        return _state(latest, account_id, None)
    return _state(latest, account_id, recheck_at(latest, verdict, settings.spam_intel))


async def open_appeal_states() -> list[SpamAppealState]:
    """Every open appeal with its re-check time, oldest first. Read-only, two queries."""
    appeals = await list_open_appeals()
    verdicts = await verdicts_for([a.account_id for a in appeals])
    cfg = settings.spam_intel
    return [
        _state(a, a.account_id, recheck_at(a, verdicts[a.account_id], cfg))
        for a in appeals
        if ended_by_clean(a, verdicts[a.account_id]) is None
    ]


async def record_appeal_filed(account_id: str, operator: str) -> SpamAppealState:
    """The operator says an appeal was filed: record who, when and what the verdict said.

    ``operator`` is the signed-in user's username. Refused (``ValueError`` with a fixed code)
    when the verdict is ``clean`` or ``frozen``, or the account is frozen or its session is dead
    (a stale reading must not register an appeal): there is no spam block to appeal. Filing while
    an appeal is open returns the open one unchanged.
    """
    account = await fetch_account(account_id)
    # The account itself: a frozen or dead session has no spam block to appeal, whatever stale
    # reading it still carries (the verdict alone cannot say).
    if account is None or health_for_status(account.status) == "fail":
        raise ValueError(NOT_LIMITED_REFUSAL)
    verdict = (await verdicts_for([account_id]))[account_id]
    if verdict.status in {"clean", "frozen"}:
        raise ValueError(NOT_LIMITED_REFUSAL)
    status: SpamStatusKind = "limited" if verdict.status == "limited" else "unknown"
    appeal, created = await file_appeal(
        account_id,
        filed_at=_appeal_close.now(),
        filed_by=operator,
        verdict=status,
        until_day=end_date(verdict.valid_until),
    )
    if created:
        await log_event(
            "INFO",
            "spam_intel_appeal_filed",
            account_id=account_id,
            extra={"verdict": status, "dated": appeal.until_day is not None},
        )
    return _state(appeal, account_id, recheck_at(appeal, verdict, settings.spam_intel))
