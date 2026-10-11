"""Account lifecycle journal — the durable record the survival counter reads.

Every write point calls :func:`record_lifecycle_event`, which never raises: the journal
is a side record, and a failure to append to it (a locked or broken database) must
never break the warming start, the graduation, the spam check or the delete that
produced the event. The failure goes to the stdlib logger, not to ``log_event``, so
a broken database does not take a second write path down with it.

Imports only ``core`` and ``schemas`` so the warming, spam-status and accounts
services can all call it without an import cycle.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, NamedTuple

from core.db import fetch_account, get_spam_status
from core.repositories.lifecycle import (
    append_lifecycle_event,
    fetch_lifecycle_snapshot,
    list_lifecycle_events,
)

if TYPE_CHECKING:
    from schemas.accounts import AccountStatus
    from schemas.lifecycle import (
        DeleteReason,
        LifecycleEvent,
        LifecycleEventKind,
        LifecycleSnapshot,
    )

logger = logging.getLogger(__name__)

# Session-check verdicts that mean the session is gone for good: Telegram revoked the
# authorization, or the account itself was deactivated/banned.
DEAD_SESSION_STATUSES: frozenset[AccountStatus] = frozenset({"unauthorized", "account_error"})


async def record_lifecycle_event(
    account_id: str,
    kind: LifecycleEventKind,
    extra: dict[str, object] | None = None,
) -> None:
    """Append one journal row; log and swallow any failure."""
    try:
        await append_lifecycle_event(account_id, kind, extra)
    except Exception as exc:  # noqa: BLE001 - the journal must never break its caller.
        logger.warning(
            "lifecycle journal write failed (kind=%s account=%s): %s",
            kind,
            account_id,
            type(exc).__name__,
            exc_info=exc,
        )


def _current_life(events: list[LifecycleEvent]) -> list[LifecycleEvent]:
    """Events since the last deletion: a re-imported id must not inherit a past death."""
    last_delete = max((i for i, e in enumerate(events) if e.kind == "deleted"), default=-1)
    return events[last_delete + 1 :]


def _is_death_event(event: LifecycleEvent) -> bool:
    if event.kind == "session_dead":
        return True
    return event.kind == "spambot_verdict" and event.extra.get("status") == "limited"


async def guess_delete_reason(account_id: str) -> DeleteReason:
    """``dead`` when @SpamBot ever limited the account or its session died, else ``retired``.

    Reads the live account status and cached spam verdict as well as the journal, so an
    account whose death predates the journal is still recognised.
    """
    account = await fetch_account(account_id)
    if account is not None and account.status in DEAD_SESSION_STATUSES:
        return "dead"
    verdict = await get_spam_status(account_id)
    if verdict is not None and verdict.status == "limited":
        return "dead"
    events = _current_life(await list_lifecycle_events(account_id))
    return "dead" if any(_is_death_event(event) for event in events) else "retired"


class PendingDeletion(NamedTuple):
    """A deletion's journal row, read before the cascade and written after it."""

    account_id: str
    extra: dict[str, object]
    snapshot: LifecycleSnapshot


async def prepare_account_deleted(
    account_id: str, reason: DeleteReason | None
) -> PendingDeletion | None:
    """Read what a deletion's row needs while the rows the cascade removes still exist.

    The guess reads the spam verdict and the snapshot reads the warming state, both of
    which the delete takes with it. Nothing is written yet: a delete that then fails must
    not leave a ``deleted`` row for a live account. ``None`` (logged) when the read fails.
    """
    try:
        resolved = reason or await guess_delete_reason(account_id)
        snapshot = await fetch_lifecycle_snapshot(account_id)
    except Exception as exc:  # noqa: BLE001 - the journal must never break the delete.
        logger.warning(
            "lifecycle delete-reason guess failed (account=%s): %s",
            account_id,
            type(exc).__name__,
            exc_info=exc,
        )
        return None
    source = "operator" if reason else "guess"
    return PendingDeletion(account_id, {"reason": resolved, "reason_source": source}, snapshot)


async def record_account_deleted(pending: PendingDeletion | None) -> None:
    """Write a prepared deletion row; call only once the delete has succeeded. Never raises."""
    if pending is None:
        return
    try:
        await append_lifecycle_event(
            pending.account_id, "deleted", pending.extra, snapshot=pending.snapshot
        )
    except Exception as exc:  # noqa: BLE001 - the account is already gone.
        logger.warning(
            "lifecycle journal write failed (kind=deleted account=%s): %s",
            pending.account_id,
            type(exc).__name__,
            exc_info=exc,
        )
