"""Lifting a confirmed spam block once @SpamBot answers ``clean`` again.

A spam block (``spam_blocked_at`` on the warming row, set when the quarantine re-check
confirmed @SpamBot's ``limited``) only lasts as long as Telegram keeps it. The first
``clean`` answer read after the mark lifts it, whoever asked: the warming cycle, the
hand-off, the survival probe or its spam-block re-check, neurocomment, or the operator's
own check on the accounts page — every one of them goes through
``services.spam_status.refresh_spam_status``, which calls :func:`lift_on_clean`.

What the lift does (the rule, ``.mex/context/runtime-warming.md``):

* the mark is cleared, so the account is back to normal: DMs allowed by the usual
  rules, the usual DM status, no «Спамблок» badge, no attention row;
* an account parked in ``spam_blocked`` goes ``idle`` — "ready to warm", not graduated.
  The stop already ended its wave in the journal (``warming_stopped``), so nothing is
  resumed: the operator starts the next wave, which the start numbers as usual. A kept
  account stays where «Оставить» put it (the warmed card, or warming again, or handed
  on); only the mark goes;
* the lift is journalled (``spam_block_lifted``) and logged.

The write is a compare-and-set on the mark (``core.repositories.warming_spam_block``):
a block confirmed again after this answer carries a newer mark and is left standing,
and racing lifts on the same answer apply once. A ``limited`` answer later on is news
again: the account re-enters the quarantine ladder like any other.

Imports only ``core``, ``schemas`` and the journal, so ``services.spam_status`` can call
it without an import cycle.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.db import fetch_warming_state, get_spam_status
from core.logging import log_event
from core.repositories.warming_spam_block import lift_spam_block_mark
from schemas.warming import lifts_spam_block
from services.lifecycle_journal import record_lifecycle_event

if TYPE_CHECKING:
    from schemas.spam_status import SpamStatusVerdict

logger = logging.getLogger(__name__)


async def lift_spam_block(account_id: str, verdict: SpamStatusVerdict) -> bool:
    """Lift the account's spam block if ``verdict`` is a ``clean`` answer newer than it.

    Returns whether this call lifted it.
    """
    if verdict.status != "clean":
        return False
    record = await fetch_warming_state(account_id)
    mark = record.spam_blocked_at if record is not None else None
    if mark is None or not lifts_spam_block(verdict, mark):
        return False
    from_state = await lift_spam_block_mark(account_id, mark)
    if from_state is None:
        return False
    await log_event(
        "INFO",
        "warming_spam_block_lifted",
        account_id=account_id,
        extra={"from_state": from_state},
    )
    await record_lifecycle_event(
        account_id,
        "spam_block_lifted",
        {"blocked_at": mark, "from_state": from_state},
    )
    return True


async def lift_on_clean(account_id: str, verdict: SpamStatusVerdict) -> None:
    """The spam check's hook: lift on a fresh ``clean``, never failing the check itself.

    A lift that fails here is not lost: the survival pass re-reads the stored answer
    (:func:`lift_from_stored_answer`) and lifts on it.
    """
    if verdict.status != "clean":
        return
    try:
        await lift_spam_block(account_id, verdict)
    except Exception as exc:  # noqa: BLE001 - a lift must never break the spam check.
        logger.warning("spam block lift failed: %s", type(exc).__name__, exc_info=exc)


async def lift_from_stored_answer(account_id: str) -> bool:
    """Lift on the stored @SpamBot answer, if it is a ``clean`` newer than the block."""
    stored = await get_spam_status(account_id)
    return stored is not None and await lift_spam_block(account_id, stored)
