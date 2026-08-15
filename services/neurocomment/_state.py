"""Transient per-account engine state — cooldowns after a flood/peer-flood.

The in-memory dicts below are the hot read path. The flood/peer-flood/slow-mode
cooldowns (``_COOLDOWN_UNTIL``, set via ``set_cooldown``) are additionally
mirrored to ``neurocomment_cooldowns`` and rehydrated at startup (#34), so a
just-flooded account stays parked across a process restart. A cooldown is only ever
removed by *expiry* (the lazy eviction in ``in_cooldown`` plus the hydrate prune): there
is deliberately no early clear, because it cannot delete the persisted row (memory and
disk would then disagree across a restart) and, with tasks sleeping in their reply delay,
it can wipe a rival task's still-live cooldown. The "channel will not let us write"
rule keeps only its failure *window* here; its rounds and pause deadline live on the
campaign link (``core.repositories.neurocomment._pauses``), because the rule
spans days and this process does not.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.repositories.neurocomment import load_active_cooldowns, persist_cooldown

if TYPE_CHECKING:
    from schemas.neurocomment import NeurocommentReadiness

# ponytail: single-process. A multi-process deployment would still need shared
# storage for the read path; the DB here is durability, not cross-process sharing.

# (account_id, channel) -> earliest UTC time it may comment again. channel=None is
# an account-wide cooldown (flood/peer-flood); a channel scopes it to that chat
# (slow-mode is per-chat, so it must not park the account everywhere).
_COOLDOWN_UNTIL: dict[tuple[str, str | None], datetime] = {}

# "This channel will not let us write" (#147), keyed by channel — consecutive write
# failures (a captcha the solver lost, or a write gate) since the last round ended. Only
# the *window* lives here: losing it on a restart costs at most one round boundary, so it
# is not worth a DB write per failure. The verdict it feeds — how many rounds the channel
# has burned and until when it is paused — is persisted on the campaign link, because a
# multi-day rule cannot be built on state a restart clears.
_WRITE_FAILED: dict[str, int] = {}


async def set_cooldown(account_id: str, until: datetime, channel: str | None = None) -> None:
    """Park ``(account, channel)`` until ``until`` (extends an existing, later cooldown).

    The in-memory map is updated first (so ``in_cooldown`` sees the deadline
    immediately and it is never lost if the durable write fails), then the row is
    persisted off the event loop via ``asyncio.to_thread`` — the single-worker
    loop must never block on the SQLite write during a flood storm. Only a
    genuinely-later deadline is written, matching the in-memory extend rule.
    """
    if until.tzinfo is None:
        # A naive datetime would ISO-serialize without an offset; the prune's
        # string comparison (``until <= now``) then breaks against aware rows.
        msg = "set_cooldown 'until' must be timezone-aware UTC"
        raise ValueError(msg)
    key = (account_id, channel)
    current = _COOLDOWN_UNTIL.get(key)
    if current is None or until > current:
        _COOLDOWN_UNTIL[key] = until
        await asyncio.to_thread(persist_cooldown, account_id, channel, until.isoformat())


def in_cooldown(account_id: str, now: datetime, channel: str | None = None) -> bool:
    """True while the account is cooled account-wide or on ``channel``.

    Lazily evicts each inspected key once it expires, so the live key set stays
    bounded. ponytail: a channel never re-checked keeps its expired key until the
    process restarts (in-memory, single-process); add a periodic sweep only if a
    long-lived listener watches very many channels.
    """
    cooled = False
    for key in {(account_id, None), (account_id, channel)}:
        until = _COOLDOWN_UNTIL.get(key)
        if until is None:
            continue
        if until > now:
            cooled = True
        else:
            del _COOLDOWN_UNTIL[key]
    return cooled


def forget_account_cooldowns(account_id: str) -> None:
    """Drop every in-memory cooldown of an account that is being deleted.

    The one exception to "a cooldown is only ever removed by expiry", and it is not an
    early clear: the account it parks is going away. ``_delete_account`` already purges
    this account's ``neurocomment_cooldowns`` rows for exactly that reason — a
    re-imported account reusing the id must not inherit a stranger's deadline — but
    ``core`` may not reach into a service module, so the memory half is dropped by the
    service that owns the delete. Without it the durable half ran backwards: a restart
    CLEARED the stale park (the row is gone) while an uptime kept it, and it was the
    live map that answered every guard.
    """
    for key in [key for key in _COOLDOWN_UNTIL if key[0] == account_id]:
        del _COOLDOWN_UNTIL[key]


async def hydrate_cooldowns() -> None:
    """Reload persisted cooldown deadlines into the in-memory map after a restart (#34).

    Called once from the NC startup reconcile. Lapsed rows are pruned in the repo;
    each surviving deadline repopulates ``_COOLDOWN_UNTIL`` so a just-flooded account
    stays parked. The in-memory map remains the hot read path thereafter.
    """
    for record in await load_active_cooldowns(datetime.now(UTC).isoformat()):
        _COOLDOWN_UNTIL[(record.account_id, record.channel)] = datetime.fromisoformat(record.until)


def channel_paused(paused_until: str | None, now: datetime) -> bool:
    """True while a persisted pause deadline is still ahead of ``now``.

    Takes the raw ISO-8601 deadline rather than a row, so the three read sites can each
    feed it from whatever they already hold — the engine and onboarding from the point
    read, the board from the channel link it lists anyway.
    """
    return paused_until is not None and datetime.fromisoformat(paused_until) > now


def awaiting_approval(readiness: NeurocommentReadiness, now: datetime) -> bool:
    """True while an admin could still land the Approve on this pair's join request.

    The patience ``_sweep._review_join_requests`` spends before it accepts that nobody is
    going to approve — its ``give_up_after``, one retry window per attempt, all measured from
    the FIRST request (the column never moves) — read as a per-pair predicate, and beside
    ``channel_paused`` for the same reason: every rule that can unlink a channel meanwhile
    (``_channel_pause``, ``_rejoin``) must hold off on exactly the pairs that review is still
    working on, and one predicate is what stops them disagreeing about which those are.

    It EXPIRES, unlike ``_onboard_pair._join_request_in_flight`` — that one answers "may we
    re-send?" and stays true forever once the attempts are gone. A request nobody ever
    answered must not hold a finished channel linked for good; past this deadline the request
    review is done with the pair and drops the channel itself.
    """
    if readiness.join_requested_at is None:
        return False
    nc = settings.neurocomment
    patience = timedelta(hours=nc.join_request_retry_hours * nc.join_request_max_attempts)
    return now - datetime.fromisoformat(readiness.join_requested_at) < patience


def register_write_failure(channel: str, *, min_failures: int) -> bool:
    """Count a write failure on ``channel``; ``True`` when it closes a round.

    The caller acts on ``True`` exactly once per round: park the channel for the flat
    pause window, or — once it has burned its last round — drop it from the campaign.
    The window resets here so the next round starts from zero.
    """
    count = _WRITE_FAILED.get(channel, 0) + 1
    if count < min_failures:
        _WRITE_FAILED[channel] = count
        return False
    _WRITE_FAILED[channel] = 0
    return True


def reset_write_failures(channel: str) -> None:
    """Zero ``channel``'s failure window — a comment was delivered, so it does let us write.

    ``register_write_failure`` counts *consecutive* failures but only clears the counter
    when a round closes. Without this, sporadic failures spread across many successes
    would accumulate to K and pause a mostly-working channel.
    """
    _WRITE_FAILED.pop(channel, None)


def reset_for_tests() -> None:
    """Test-only reset; production code never calls this."""
    _COOLDOWN_UNTIL.clear()
    _WRITE_FAILED.clear()
