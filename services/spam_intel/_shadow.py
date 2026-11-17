"""The shadow comparison (design 7, Stage 2): the fold's word against the real @SpamBot answer.

While ``SPAM_INTEL__PASSIVE_DECISIONS`` is off the fold only shows. For the first weeks the owner
wants to see how often it would have been right, so each real @SpamBot reading logs one
``spam_intel_shadow_comparison`` event: the fold's status just BEFORE the reading, the real
status, whether they agree, and the evidence kind the fold stood on. Enumerated values only,
never a reply's text. :func:`shadow_agreement` tallies those events; it reads the log table and
sends nothing to Telegram.

Nothing here may fail a probe: both halves swallow and log by exception class.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from core.repositories.logs import list_filtered_logs
from schemas.logs import LogFilter
from schemas.spam_intel import ShadowAgreement
from services.spam_intel._evidence import current_view

if TYPE_CHECKING:
    from schemas.spam_evidence import Verdict
    from schemas.spam_status import SpamStatusKind

logger = logging.getLogger(__name__)

SHADOW_EVENT = "spam_intel_shadow_comparison"
_TALLY_LIMIT = 1000


def shadow_on() -> bool:
    """The comparison runs only while the fold is a shadow (``passive_decisions`` off)."""
    intel = settings.spam_intel
    return intel.enabled and not intel.passive_decisions


async def fold_before(account_id: str) -> Verdict | None:
    """The fold's verdict now, to be compared with the reading about to be taken."""
    if not shadow_on():
        return None
    try:
        return (await current_view(account_id))[0]
    except Exception as exc:  # noqa: BLE001 - observation must never fail a probe.
        logger.warning("spam shadow read failed: %s", type(exc).__name__)
        return None


async def record_comparison(account_id: str, before: Verdict | None, real: SpamStatusKind) -> None:
    """Log the fold's earlier verdict against the real one (nothing when there was no fold)."""
    if before is None:
        return
    try:
        await log_event(
            "INFO",
            SHADOW_EVENT,
            account_id=account_id,
            extra={
                "fold": before.status,
                "real": real,
                "agree": before.status == real,
                "primary_kind": before.primary_kind,
            },
        )
    except Exception as exc:  # noqa: BLE001 - observation must never fail a probe.
        logger.warning("spam shadow log failed: %s", type(exc).__name__)


async def shadow_agreement(limit: int = _TALLY_LIMIT) -> ShadowAgreement:
    """Agreement of the fold with real answers over the latest ``limit`` comparisons."""
    entries = await list_filtered_logs(LogFilter(event_prefix=SHADOW_EVENT, limit=limit))
    rows: dict[str, int] = {}
    agree = 0
    for entry in entries:
        key = f"{entry.extra.get('fold')}->{entry.extra.get('real')}"
        rows[key] = rows.get(key, 0) + 1
        agree += 1 if entry.extra.get("agree") is True else 0
    return ShadowAgreement(total=len(entries), agree=agree, rows=rows)
