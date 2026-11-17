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
  says the block is gone). The gateway's store path calls :func:`close_on_clean`; reading the
  state also catches up on a ``clean`` stored by a path that did not call it. Closing never lifts
  a block: that stays ``services.spam_block_lift``.
* The re-check (owner decision D15) is a time this module names; ``_appeal_recheck`` asks the
  gateway at it, an ordinary DECISION ask under the budget.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import get_spam_status
from core.logging import log_event
from core.repositories.spam_appeals import (
    fetch_latest_appeal,
    fetch_open_appeal,
    file_appeal,
    list_open_appeals,
)
from schemas.spam_appeals import SpamAppeal, SpamAppealState
from services.spam_intel import _appeal_close
from services.spam_intel._appeal_close import close_on_clean
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


def recheck_at(appeal: SpamAppeal, verdict: Verdict | None, cfg: SpamIntelSettings) -> datetime:
    """When an open appeal is asked about again: the end day plus a jitter, or hours after filing.

    A dated limit is re-checked at its end (the later of the day on file and the day the current
    verdict names); an open-ended one ``appeal_recheck_hours`` after the operator filed.
    """
    ends = [_end_of(appeal.until_day)] if appeal.until_day is not None else []
    if verdict is not None and verdict.status == "limited" and verdict.valid_until is not None:
        ends.append(verdict.valid_until)
    if ends:
        return max(ends) + _jitter(appeal, cfg)
    return appeal.filed_at + timedelta(hours=cfg.appeal_recheck_hours)


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


async def _catch_up(appeal: SpamAppeal) -> bool:
    """Close the appeal on a stored @SpamBot ``clean`` newer than the filing; ``True`` if it did.

    A passive ``clean`` never writes the stored status (owner decision D4), so a ``clean`` there
    IS a real answer. This covers a reading stored by a path that did not call
    :func:`close_on_clean` (the gateway switched off, a legacy caller).
    """
    stored = await get_spam_status(appeal.account_id)
    if stored is None or stored.status != "clean" or _stamp(stored.checked_at) < appeal.filed_at:
        return False
    return await close_on_clean(appeal.account_id, _stamp(stored.checked_at))


async def open_appeal(account_id: str) -> SpamAppeal | None:
    """The account's open appeal (after catching up on a ``clean`` already stored), or ``None``."""
    appeal = await fetch_open_appeal(account_id)
    if appeal is None or await _catch_up(appeal):
        return None
    return appeal


async def appeal_state(account_id: str) -> SpamAppealState:
    """What the card shows: the newest appeal, whether it is open, and its re-check time."""
    await open_appeal(account_id)  # closes a stale one first
    latest = await fetch_latest_appeal(account_id)
    if latest is None or latest.closed_at is not None:
        return _state(latest, account_id, None)
    verdict = (await verdicts_for([account_id]))[account_id]
    return _state(latest, account_id, recheck_at(latest, verdict, settings.spam_intel))


async def open_appeal_states() -> list[SpamAppealState]:
    """Every open appeal with its re-check time, oldest first."""
    appeals = [a for a in await list_open_appeals() if await open_appeal(a.account_id) is not None]
    verdicts = await verdicts_for([a.account_id for a in appeals])
    cfg = settings.spam_intel
    return [_state(a, a.account_id, recheck_at(a, verdicts[a.account_id], cfg)) for a in appeals]


async def record_appeal_filed(account_id: str, operator: str) -> SpamAppealState:
    """The operator says an appeal was filed: record who, when and what the verdict said.

    ``operator`` is the signed-in user's username. Refused (``ValueError`` with a fixed code)
    when the verdict is ``clean`` or ``frozen``: there is no spam block to appeal. Filing while
    an appeal is open returns the open one unchanged.
    """
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
