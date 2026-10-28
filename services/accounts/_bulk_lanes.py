"""One account's lane in a bulk run, what each refusal means, and one-lane item moves.

The moves: a retry on the same account, and undoing a cut-off hand-over.

Split out of ``services.accounts._bulk_runner`` for the file-size budget; the runner
and the lead queue read the codes from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.repositories import bulk_runs as repo

if TYPE_CHECKING:
    from schemas.bulk_messages import BulkMessageOutcome
    from schemas.bulk_runs import BulkRunAccount, BulkRunItem, WaitReason

# Statuses where the message may have left the account.
SENT_STATUSES = frozenset({"ok", "unconfirmed"})


@dataclass
class Lane:
    row: BulkRunAccount
    sending: bool = False
    on_break: bool = False
    # First messages left in ``day``; ``None`` when the run has no daily cap.
    left: int | None = None
    day: str = ""
    young: bool = False  # half of the run's daily cap
    # Refusals in a row that sent nothing and are worth another try (``RETRY_CODES``).
    retries: int = 0

    def ready_at(self) -> datetime | None:
        return None if self.row.ready_at is None else datetime.fromisoformat(self.row.ready_at)


@dataclass
class WaitCandidate:
    wake_at: datetime | None = None
    reason: WaitReason | None = None

    def offer(self, moment: datetime, reason: WaitReason) -> None:
        if self.wake_at is None or moment < self.wake_at:
            self.wake_at, self.reason = moment, reason


# Codes where the account provably sent nothing and cannot send for the rest of the
# run: stop using it (split hands the recipient on). "unconfirmed" never lands here.
# A code also in ``RETRY_CODES`` stops it only after that many retries in a row.
STOP_CODES = frozenset(
    {
        "flood_wait",
        "slow_mode_wait",
        "premium_wait",
        "peer_flood",
        "account_not_found",
        "session_dead",
        "account_deactivated",
        "account_frozen",
        "unavailable",
    }
)
# A dropped connection or a chat's slow mode: nothing left, nothing learnt about the
# account. The same recipient is tried again after a wait.
RETRY_CODES = frozenset({"slow_mode_wait", "unavailable"})


async def retry_later(
    item: BulkRunItem, lane: Lane, outcome: BulkMessageOutcome, now: datetime
) -> bool:
    """A refusal that sent nothing and says nothing about the account: try again.

    The recipient goes back to pending on the same account, which waits the slow
    mode's own time or the retry delay. Only enough of them in a row stop it.
    """
    limits = settings.bulk_messages
    if outcome.error_code not in RETRY_CODES or lane.retries >= limits.max_transient_retries:
        return False
    lane.retries += 1
    wait = outcome.retry_after_seconds or limits.transient_retry_seconds
    item.status = "pending"
    await repo.save_bulk_run_item(item)
    lane.row.ready_at = (now + timedelta(seconds=wait)).isoformat()
    await repo.save_bulk_run_account(lane.row)
    return True


async def undo_unsent_hand_overs(items: list[BulkRunItem]) -> None:
    """A hand-over the cancel cut off never happened: the refusal is the final word."""
    for retry in [i for i in items if i.status == "pending"]:
        if retry.id is None:
            continue
        refused = next(
            (
                i
                for i in items
                if i.handed_over and i.recipient == retry.recipient and i.ordinal == retry.ordinal
            ),
            None,
        )
        if refused is None:
            continue
        refused.handed_over = False
        await repo.save_bulk_run_item(refused)
        await repo.delete_bulk_run_item(retry.id)
        items.remove(retry)
