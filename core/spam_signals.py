"""The port through which ``core`` reports what an account's normal work showed (spam intel).

``core`` must not import ``services``, yet a PeerFlood seen by the executor is evidence that
``services.spam_intel`` folds. So ``core`` calls :func:`emit_signal` and the app registers the
receiver once at startup (:func:`set_signal_sink`); with none registered the call does nothing.

An emission is a fire-and-forget task: the caller never awaits I/O, never sees the sink's result
and never sees its failure (logged by class name only). The action that raised the signal returns
exactly what it would have returned without it. Nothing here makes a Telegram request.

A task can outlive the account it was raised for. Two things keep it from reviving that account's
evidence: :func:`discard_signals_for` (the account delete calls it) cancels the account's tasks
still in flight, and the receiver reads :func:`emitted_at` (the instant the signal was raised,
as seen from inside its task) so the repository can refuse it when the account row is gone or was
created after that instant (a new account that reuses the id).
"""

from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Protocol

from core.config import settings

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.spam_evidence import EvidenceKind

logger = logging.getLogger(__name__)


class SignalSink(Protocol):
    """What the receiver looks like: ``services.spam_intel.record_evidence`` fits it."""

    def __call__(
        self,
        account_id: str,
        kind: EvidenceKind,
        *,
        source: str,
        operation: str | None = None,
        detail: str | None = None,
    ) -> Awaitable[None]: ...


class _Registry:
    sink: SignalSink | None = None
    # Strong references: the loop keeps only weak ones to a task nobody awaits. The value is the
    # account the task reports on, so an account's tasks can be cancelled with the account.
    pending: dict[asyncio.Task[None], str] = {}  # noqa: RUF012 - one registry per process.


_registry = _Registry()
_EMITTED_AT: ContextVar[datetime | None] = ContextVar("spam_signal_emitted_at", default=None)


def emitted_at() -> datetime | None:
    """When the signal being delivered was raised (inside a delivery task), else ``None``."""
    return _EMITTED_AT.get()


def set_signal_sink(sink: SignalSink | None) -> None:
    """Register the receiver (``None`` removes it). Calling it again replaces it: idempotent."""
    _registry.sink = sink


def reset_signals_for_tests() -> None:
    """Remove the receiver and forget the tasks in flight."""
    _registry.sink = None
    _registry.pending.clear()


async def _deliver(call: Callable[[], Awaitable[None]], raised_at: datetime) -> None:
    _EMITTED_AT.set(raised_at)  # this task's own context: it reaches only this delivery
    try:
        await call()
    except Exception as exc:  # noqa: BLE001 - a signal must never reach the action that raised it.
        logger.warning("spam signal not delivered: %s", type(exc).__name__)


def _forget(task: asyncio.Task[None]) -> None:
    _registry.pending.pop(task, None)


def discard_signals_for(account_id: str) -> int:
    """Cancel the signals still in flight for an account (it is being deleted); how many.

    A task already inside its database write cannot be stopped from here; the repository's
    account check refuses what it would still write (``emitted_at``).
    """
    stale = [task for task, owner in _registry.pending.items() if owner == account_id]
    for task in stale:
        task.cancel()
    return len(stale)


def emit_signal(
    account_id: str,
    kind: EvidenceKind,
    *,
    source: str,
    operation: str | None = None,
    detail: str | None = None,
) -> None:
    """Report one observation. Returns at once and never raises.

    ``source`` names the gateway that saw it, ``operation`` the action family, ``detail`` a short
    fact with no third-party text. Does nothing without a receiver, outside a running event loop,
    or while ``SPAM_INTEL__ENABLED`` / ``SPAM_INTEL__PASSIVE_SIGNALS`` is off.
    """
    sink = _registry.sink
    intel = settings.spam_intel
    if sink is None or not (intel.enabled and intel.passive_signals):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # no running loop: nothing to schedule on
        return
    call = partial(sink, account_id, kind, source=source, operation=operation, detail=detail)
    task = loop.create_task(_deliver(call, datetime.now(UTC)))
    _registry.pending[task] = account_id
    task.add_done_callback(_forget)


async def drain_signals() -> None:
    """Wait for the emissions in flight (shutdown and tests); never raises."""
    pending = list(_registry.pending)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
