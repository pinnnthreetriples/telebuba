"""The stored spam status as a DECISION reads it (docs/spam-intel.md sections 5.12, 6).

Every reader that skips, docks or refuses an account on its stored status (outreach protection,
the masstagging fleet, trust, neurocomment's pool, the warming board, the accounts table) reads
it through :func:`decision_statuses` / :func:`decision_status` over what its own seam already
loaded. One small helper, not a rewrite of the readers.

* ``SPAM_INTEL__PASSIVE_DECISIONS`` off (the default, the shadow period): the stored rows come
  back untouched and nothing is read. The readers behave exactly as before the evidence layer.
* On: the fold, with the clock applied, decides what a stored row alone cannot. A fold that says
  ``limited`` or ``frozen`` blocks (``limited``; a freeze says ``frozen`` in the detail). A dated
  limit that has ended reads ``unknown`` (``limit_expired``). Anything else keeps the stored
  row: a passive ``clean`` never promotes an ``unknown`` or lifts a ``limited`` (owner decision
  D4), and an account the fold has no opinion on stays absent exactly as it was.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from schemas.spam_status import SpamStatusVerdict
from services.spam_intel import _evidence

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import datetime

    from schemas.spam_evidence import Verdict

FROZEN_DETAIL = "frozen"
EXPIRED_DETAIL = "limit_expired"


def decisions_on() -> bool:
    """Whether the fold decides (``SPAM_INTEL__ENABLED`` and ``SPAM_INTEL__PASSIVE_DECISIONS``)."""
    intel = settings.spam_intel
    return intel.enabled and intel.passive_decisions


def decision_view(
    account_id: str,
    stored: SpamStatusVerdict | None,
    verdict: Verdict,
    at: datetime,
) -> SpamStatusVerdict | None:
    """The status a decision reads for one account: the stored row, changed only by the fold."""
    stamp = stored.checked_at if stored is not None else at.isoformat()
    if verdict.status == "frozen":
        return SpamStatusVerdict(
            account_id=account_id, status="limited", detail=FROZEN_DETAIL, checked_at=stamp
        )
    if verdict.status == "limited":
        if stored is not None and stored.status == "limited":
            return stored  # keeps the reply's own detail (the date) for the card
        return SpamStatusVerdict(
            account_id=account_id, status="limited", detail=verdict.primary_kind, checked_at=stamp
        )
    if verdict.reason == "limit_expired":
        return SpamStatusVerdict(
            account_id=account_id, status="unknown", detail=EXPIRED_DETAIL, checked_at=stamp
        )
    return stored


async def decision_statuses(
    account_ids: Iterable[str],
    stored: Mapping[str, SpamStatusVerdict],
) -> dict[str, SpamStatusVerdict]:
    """``stored`` (what the caller's seam read) as decisions read it, for ``account_ids``."""
    if not decisions_on():
        return dict(stored)
    ids = list(dict.fromkeys(account_ids))
    verdicts = await _evidence.verdicts_for(ids)
    at = _evidence.now()
    result: dict[str, SpamStatusVerdict] = {}
    for account_id in ids:
        view = decision_view(account_id, stored.get(account_id), verdicts[account_id], at)
        if view is not None:
            result[account_id] = view
    return result


async def decision_status(
    account_id: str,
    stored: SpamStatusVerdict | None,
) -> SpamStatusVerdict | None:
    """One account's stored status as a decision reads it (see :func:`decision_statuses`)."""
    if not decisions_on():
        return stored
    views = await decision_statuses([account_id], {} if stored is None else {account_id: stored})
    return views.get(account_id)
