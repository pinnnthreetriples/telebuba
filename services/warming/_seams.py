"""Injectable engine seams, centralised so tests patch them in one place.

The warming engine reaches Telegram (``execute``), Gemini (``generate_text``),
the spam probe (``refresh_spam_status``) and randomness (``_rng``) only through
this module, so a test patches ``services.warming._seams.<name>`` once and every
engine submodule observes it. Before the package split these lived directly on
the ``services.warming`` namespace; the seam module preserves single-point
patching across the split submodules.
"""

from __future__ import annotations

import asyncio
import random
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from core.gemini import generate_text
from core.openai import generate_text_deepseek
from core.telegram_client import execute as _gateway_execute
from services.spam_status import refresh_spam_status as _refresh_spam_status

if TYPE_CHECKING:
    from collections.abc import Iterator

    from schemas.spam_status import SpamStatusVerdict
    from schemas.telegram_actions import ActionResult, TelegramAction

# A loop task carries its generation in a context variable, while the runtime
# registry carries the only generation currently allowed to dispatch.  Stop,
# restart and shutdown revoke the registry entry *before* cancellation.  This
# closes the gap where a coroutine catches ``CancelledError`` and otherwise
# keeps issuing Telegram requests after the operator has stopped it.
_LEASE: ContextVar[tuple[str, str] | None] = ContextVar("warming_lease", default=None)
_ACTIVE_LEASES: dict[str, str] = {}


class WarmingLeaseRevokedError(RuntimeError):
    """A stale warming generation attempted external I/O after lease revocation."""


def activate_lease(account_id: str, run_id: str) -> None:
    """Allow one runtime generation to dispatch for ``account_id``."""
    _ACTIVE_LEASES[account_id] = run_id


def revoke_lease(account_id: str, run_id: str | None = None) -> None:
    """Deny dispatch for an account, optionally only for the named generation."""
    if run_id is None or _ACTIVE_LEASES.get(account_id) == run_id:
        _ACTIVE_LEASES.pop(account_id, None)


@contextmanager
def lease_scope(account_id: str, run_id: str | None) -> Iterator[None]:
    """Bind a spawned warming loop to its runtime generation."""
    if run_id is None:
        yield
        return
    token = _LEASE.set((account_id, run_id))
    try:
        yield
    finally:
        _LEASE.reset(token)


def _assert_live_lease(account_id: str) -> None:
    lease = _LEASE.get()
    # Direct/test calls to ``run_one_cycle`` do not own a long-running runtime
    # lease.  Their existing explicit lifetime remains unchanged.
    if lease is None:
        return
    lease_account_id, run_id = lease
    if lease_account_id != account_id or _ACTIVE_LEASES.get(account_id) != run_id:
        raise WarmingLeaseRevokedError(account_id)


async def execute(account_id: str, action: TelegramAction) -> ActionResult:
    """Dispatch a warming Telegram action only while its generation owns the lease.

    The second check handles a cancellation-suppressing gateway call that was
    already in flight when Stop revoked the lease.  Its uncertain outcome is
    failed closed by raising instead of allowing the stale cycle to continue.
    """
    _assert_live_lease(account_id)
    result = await _gateway_execute(account_id, action, domain="warming")
    _assert_live_lease(account_id)
    return result


async def refresh_spam_status(account_id: str, *, force: bool = False) -> SpamStatusVerdict:
    """Fence the quarantine Telegram probe with the same runtime lease."""
    _assert_live_lease(account_id)
    result = await _refresh_spam_status(account_id, force=force)
    _assert_live_lease(account_id)
    return result


def reset_leases_for_tests() -> None:
    """Clear process-local ownership between isolated event-loop tests."""
    _ACTIVE_LEASES.clear()


# SystemRandom: non-cryptographic jitter/selection; avoids ruff S311 on the
# module-level ``random.*`` helpers. Behaviour is identical for our needs.
rng = random.SystemRandom()


async def sleep(seconds: float) -> None:
    """Async sleep seam — patched to a no-op in tests so delays stay instant."""
    await asyncio.sleep(seconds)


__all__ = [
    "execute",
    "generate_text",
    "generate_text_deepseek",
    "refresh_spam_status",
    "rng",
    "sleep",
]
