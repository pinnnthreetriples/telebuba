"""What one warming cycle reads, read once (Прогрев 2.0, item 5.1).

A cycle used to ask the database the same questions from every step — the account
eight times, the state row a dozen, the trust inputs twice — about forty reads for
one visit. Now the iteration loads a ``_CycleSnapshot`` after its first gates and
publishes it for the rest of the cycle; the steps take the account, settings,
channels, joined channels and trust inputs from it instead of querying again.

It is published per task in a ``ContextVar`` — the same mechanism the seams use for
the lease and the device — so it never leaks into another account's loop, and a
direct call outside an iteration (most tests, the board, the runner) finds nothing
and reads as before. Only facts a cycle does not change itself live here; the state
row the settle CAS-checks is always read fresh, and the settle re-reads what the
cycle may have changed (``refresh_after_cycle``).
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.phone_geo import timezone_for_phone
from core.repositories.accounts import fetch_account
from core.repositories.device_fingerprint import fetch_device_fingerprint
from core.repositories.warming import list_warming_channels
from core.repositories.warming_floods import fetch_warming_flood_summary
from core.repositories.warming_joined import list_joined_channels
from schemas.trust import TrustScore
from services.trust import account_trust_score_from

if TYPE_CHECKING:
    from collections.abc import Iterator

    from schemas._warming_extras import JoinedChannel
    from schemas.accounts import AccountRead
    from schemas.spam_status import SpamStatusVerdict
    from schemas.warming import WarmingChannel, WarmingSettingsSecret, WarmingStateRecord
    from schemas.warming_flood import WarmingFloodSummary

# The join cap is a rolling day, not a calendar one: a join at 23:59 still counts
# against the next morning's cycles.
JOIN_CAP_WINDOW = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class _CycleSnapshot:
    """Everything one cycle reads, as it stood when the cycle began."""

    account_id: str
    controls: WarmingSettingsSecret
    record: WarmingStateRecord | None
    account: AccountRead | None
    spam: SpamStatusVerdict | None
    lang_code: str | None
    floods: WarmingFloodSummary | None
    # Approved channels only, as ``list_warming_channels`` returns them.
    channels: tuple[WarmingChannel, ...]
    # Every channel the account ever joined through warming, left ones included.
    joined: tuple[JoinedChannel, ...]

    @property
    def tz(self) -> str | None:
        """The account's IANA timezone (from its phone number), or ``None``."""
        return timezone_for_phone(self.account.phone) if self.account else None

    def is_joined(self, channel: str) -> bool:
        """Joined and not left since — ``is_channel_joined`` over the loaded rows."""
        return any(j.channel == channel and j.left_at is None for j in self.joined)

    def joins_since(self, since: datetime) -> int:
        """``count_joins_since`` over the loaded rows (ISO strings compare as the SQL did)."""
        cutoff = since.astimezone(UTC).isoformat()
        return sum(1 for j in self.joined if j.created_at >= cutoff)

    def trust(self, now: datetime, record: WarmingStateRecord | None = None) -> TrustScore:
        """The Trust Score from the loaded signals; ``record`` overrides the stored row."""
        if self.account is None:
            return TrustScore(
                account_id=self.account_id,
                score=0,
                band="critical",
                reasons=["unknown account"],
            )
        return account_trust_score_from(
            account=self.account,
            record=record if record is not None else self.record,
            spam=self.spam,
            lang_code=self.lang_code,
            now=now,
            floods=self.floods,
        )


_CURRENT: ContextVar[_CycleSnapshot | None] = ContextVar("warming_cycle_snapshot", default=None)


async def load_cycle_snapshot(
    account_id: str,
    controls: WarmingSettingsSecret,
    record: WarmingStateRecord | None,
    spam: SpamStatusVerdict | None,
) -> _CycleSnapshot:
    """Read the rest of the cycle's data once; the gates already read settings, row, spam."""
    fingerprint = await fetch_device_fingerprint(account_id)
    return _CycleSnapshot(
        account_id=account_id,
        controls=controls,
        record=record,
        account=await fetch_account(account_id),
        spam=spam,
        lang_code=fingerprint.system_lang_code if fingerprint else None,
        floods=await fetch_warming_flood_summary(account_id),
        channels=tuple((await list_warming_channels()).channels),
        joined=tuple(await list_joined_channels(account_id)),
    )


@contextmanager
def snapshot_scope() -> Iterator[None]:
    """One iteration's lifetime: whatever it publishes is gone when it ends."""
    token = _CURRENT.set(None)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def publish_snapshot(snapshot: _CycleSnapshot) -> None:
    """Make ``snapshot`` the current cycle's data for the rest of this iteration."""
    _CURRENT.set(snapshot)


def current_snapshot(account_id: str) -> _CycleSnapshot | None:
    """The running cycle's snapshot of ``account_id``, or ``None`` outside a cycle."""
    snapshot = _CURRENT.get()
    return snapshot if snapshot is not None and snapshot.account_id == account_id else None


async def refresh_after_cycle(account_id: str, *, flooded: bool) -> _CycleSnapshot | None:
    """Re-read what a cycle may change before the settle scores it again.

    A dead session marks the account mid-cycle and a peer flood was just added to
    the flood memory; the rest of the snapshot still holds. ``None`` outside a cycle.
    """
    snapshot = current_snapshot(account_id)
    if snapshot is None:
        return None
    fresh = replace(snapshot, account=await fetch_account(account_id))
    if flooded:
        fresh = replace(fresh, floods=await fetch_warming_flood_summary(account_id))
    publish_snapshot(fresh)
    return fresh
