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

from core.db import fetch_account
from core.repositories.death_evidence import first_failure_after, latest_telegram_success_at
from core.repositories.lifecycle import (
    append_lifecycle_event,
    fetch_lifecycle_snapshot,
    list_lifecycle_events,
)

if TYPE_CHECKING:
    from schemas.accounts import AccountRead, AccountStatus
    from schemas.lifecycle import (
        DeleteReason,
        LifecycleEvent,
        LifecycleEventKind,
        LifecycleSnapshot,
    )

logger = logging.getLogger(__name__)

# Session-check verdicts that mean the account is gone for good: Telegram revoked the
# authorization, deactivated or banned the account, or froze it.
DEAD_SESSION_STATUSES: frozenset[AccountStatus] = frozenset(
    {"unauthorized", "account_error", "frozen"},
)
# The gateway's stable refusal codes that show the same death on an action.
_FROZEN_CODES = ("account_frozen",)
_GONE_CODES = ("session_dead", "account_deactivated")
_DEATH_CODES = (*_FROZEN_CODES, *_GONE_CODES)
_ANSWERS = frozenset({"clean", "limited"})


async def record_lifecycle_event(
    account_id: str,
    kind: LifecycleEventKind,
    extra: dict[str, object] | None = None,
    snapshot: LifecycleSnapshot | None = None,
) -> None:
    """Append one journal row; log and swallow any failure.

    ``snapshot`` pins the user id and stint to what the caller saw under its own lock,
    instead of re-reading state that a concurrent restart may already have replaced.
    """
    try:
        await append_lifecycle_event(account_id, kind, extra, snapshot)
    except Exception as exc:  # noqa: BLE001 - the journal must never break its caller.
        logger.warning(
            "lifecycle journal write failed (kind=%s account=%s): %s",
            kind,
            account_id,
            type(exc).__name__,
            exc_info=exc,
        )


def current_life(events: list[LifecycleEvent], born_at: str | None) -> list[LifecycleEvent]:
    """Events of the live account only: a re-imported id must not inherit a past death.

    Two independent boundaries, because the ``deleted`` row is best-effort and may be
    missing: nothing before the last deletion, and nothing before the live account row
    was created (``born_at``, ISO like every journal timestamp).
    """
    last_delete = max((i for i, e in enumerate(events) if e.kind == "deleted"), default=-1)
    since_delete = events[last_delete + 1 :]
    if born_at is None:
        return since_delete
    return [event for event in since_delete if event.created_at >= born_at]


async def guess_delete_reason(account_id: str) -> DeleteReason:
    """``dead`` when the account's session died for good, else ``retired``.

    Reads the live account status as well as the journal, so an account whose death
    predates the journal is still recognised. A spam block is NOT a death: the account
    still works, so deleting one is the operator writing it off.
    """
    account = await fetch_account(account_id)
    if account is not None and account.status in DEAD_SESSION_STATUSES:
        return "dead"
    born_at = account.created_at if account is not None else None
    events = current_life(await list_lifecycle_events(account_id), born_at)
    return "dead" if any(event.kind == "session_dead" for event in events) else "retired"


async def record_session_death(account: AccountRead, status: AccountStatus) -> None:
    """Journal that a session check found ``account`` gone for good — once per death.

    Written on the transition into a dead status, and also when the account already
    wears one with no death on its life's record: a Telegram action can mark an account
    ``frozen`` without any session check seeing it. A ``new`` import never lived.
    The row's ``created_at`` is when the death was found; ``extra.evidence_at`` (when
    one is on record, see :func:`death_evidence`) is when it was first shown, which the
    survival counter dates the death by. Never raises (the journal never breaks its
    caller).
    """
    if status not in DEAD_SESSION_STATUSES or account.status == "new":
        return
    try:
        life = current_life(await list_lifecycle_events(account.account_id), account.created_at)
    except Exception as exc:  # noqa: BLE001 - the journal must never break its caller.
        logger.warning("lifecycle journal read failed: %s", type(exc).__name__, exc_info=exc)
        if account.status in DEAD_SESSION_STATUSES:
            return  # cannot tell whether this death is already on the record
        life = []
    if account.status in DEAD_SESSION_STATUSES and any(
        event.kind == "session_dead" for event in life
    ):
        return
    codes = _FROZEN_CODES if status == "frozen" else _GONE_CODES
    extra: dict[str, object] = {"status": status}
    extra |= await death_evidence(account.account_id, codes, alive_floor(life, account.created_at))
    await record_lifecycle_event(account.account_id, "session_dead", extra)


def alive_floor(life: list[LifecycleEvent], born_at: str | None) -> str | None:
    """The latest moment the journal proves this life alive: its start or an answer."""
    moments = [born_at] if born_at else []
    moments += [
        event.created_at
        for event in life
        if event.kind == "spambot_verdict" and event.extra.get("status") in _ANSWERS
    ]
    return max(moments, default=None)


async def death_evidence(
    account_id: str,
    codes: tuple[str, ...],
    floor: str | None,
) -> dict[str, object]:
    """When a death found only now was first recorded: ``evidence_at`` and its event.

    The earliest logged Telegram refusal that showed the death (one of ``codes``), but
    never one at or before the last thing that proved the account alive: a logged
    action that succeeded, an @SpamBot answer or the life's start (``floor``). Without
    such a refusal the death is dated by its detection, so nothing is returned and the
    journal row's own ``created_at`` stands. Never raises: the journal never breaks its
    caller, and a death without evidence is still a death.
    """
    try:
        success = await latest_telegram_success_at(account_id)
        after = max((m for m in (success, floor) if m is not None), default=None)
        row = await first_failure_after(account_id, codes, after)
    except Exception as exc:  # noqa: BLE001 - the journal must never break its caller.
        logger.warning("death evidence read failed: %s", type(exc).__name__, exc_info=exc)
        return {}
    if row is None:
        return {}
    return {"evidence_at": row.created_at, "evidence_event": row.event}


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
    extra: dict[str, object] = {
        "reason": resolved,
        "reason_source": "operator" if reason else "guess",
    }
    if reason is None and resolved == "dead":
        # Guessed dead only ever on a dead session now; survival trusts a guess with this
        # evidence and treats older guesses (made on a spam limit) as write-offs.
        extra["evidence"] = "session"
    if resolved == "dead":
        extra |= await _deletion_evidence(account_id)
    return PendingDeletion(account_id, extra, snapshot)


async def _deletion_evidence(account_id: str) -> dict[str, object]:
    """A death the operator states by deleting is dated like one a check found later."""
    try:
        account = await fetch_account(account_id)
        born_at = account.created_at if account is not None else None
        life = current_life(await list_lifecycle_events(account_id), born_at)
    except Exception as exc:  # noqa: BLE001 - the journal must never break the delete.
        logger.warning("lifecycle journal read failed: %s", type(exc).__name__, exc_info=exc)
        return {}
    return await death_evidence(account_id, _DEATH_CODES, alive_floor(life, born_at))


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
