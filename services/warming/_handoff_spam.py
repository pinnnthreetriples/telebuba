"""The @SpamBot precondition of the hand-off to neurocomment.

A hand-off needs a ``clean`` answer, never merely the absence of a ``limited`` one: an
account nobody asked is not a clean account. There is deliberately no "hand off without
the check" escape — an unreadable answer refuses, and the operator retries.

A ``limited`` answer takes the account off the warmed card and back into warming's
quarantine, where the recovery ladder re-asks after the quarantine window like it does
for a peer flood. Runs under the caller's per-account lifecycle lock.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    fetch_warming_state,
    get_listener_account_id,
    get_listener_running,
    unmark_promoted_to_nc,
)
from core.logging import log_event
from services.warming import _exclusion, _runtime
from services.warming._spam_check import ask_spambot
from services.warming._state import _set_state

if TYPE_CHECKING:
    from schemas.warming import WarmingRefusalCode

SPAM_CHECK_UNAVAILABLE: WarmingRefusalCode = "spam_check_unavailable"
SPAM_LIMITED: WarmingRefusalCode = "spam_limited"


class HandoffSpamRefusedError(RuntimeError):
    """The hand-off was refused on its @SpamBot check; ``code`` names why.

    Not a ``ValueError``: the hand-off route maps those to 400 (not graduated), and
    both of these are 409 answers about the account's standing right now.
    """

    def __init__(self, code: WarmingRefusalCode, account_id: str) -> None:
        self.code = code
        super().__init__(f"{code}: {account_id}")


async def _held_elsewhere(account_id: str) -> bool:
    """True while another runtime holds the session (listener, discovery, a campaign)."""
    if await get_listener_running() and await get_listener_account_id() == account_id:
        return True
    try:
        _exclusion.assert_no_discovery_run(account_id)
        _exclusion.assert_not_campaign_held(account_id)
    except _exclusion.AccountUnavailableError:
        return True
    return False


async def _quarantine_for_handoff(account_id: str) -> None:
    """Put a limited account back into warming's quarantine with a running loop.

    When another feature holds the session the state is stored without a loop:
    reconciliation at the next restart picks the quarantine row up.
    """
    now = datetime.now(UTC)
    held = await _held_elsewhere(account_id)
    run_id = None if held else uuid.uuid4().hex
    await _set_state(
        account_id,
        "quarantine",
        last_event="handoff_spam_limited",
        next_run_at=(now + timedelta(hours=settings.warming.quarantine_hours)).isoformat(),
        heartbeat_at=now.isoformat(),
        stopped_at=None,
        last_error="spam limited",
        quarantine_count=0,
        run_id=run_id,
    )
    if run_id is not None:
        _runtime._spawn_runtime_task(account_id, run_id)  # noqa: SLF001 - shared lifecycle primitive.
    await _runtime._refresh_dialogue_pairs()  # noqa: SLF001 - patchable seam, reached via the module.


async def assert_spam_clean_for_handoff(account_id: str) -> None:
    """Refuse the hand-off unless @SpamBot answers ``clean`` (a reading ≤ TTL is reused).

    An account not on the warmed card is left for the hand-off write to refuse, so a
    stale click never costs a @SpamBot question.
    """
    record = await fetch_warming_state(account_id)
    if record is None or not record.promoted_to_nc:
        return
    verdict = await ask_spambot(account_id)
    if verdict.status == "clean":
        return
    if verdict.status == "limited":
        await unmark_promoted_to_nc(account_id)
        await _quarantine_for_handoff(account_id)
        await log_event("WARNING", "warming_handoff_spam_limited", account_id=account_id)
        raise HandoffSpamRefusedError(SPAM_LIMITED, account_id)
    await log_event("WARNING", "warming_handoff_spam_unavailable", account_id=account_id)
    raise HandoffSpamRefusedError(SPAM_CHECK_UNAVAILABLE, account_id)
