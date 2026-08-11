"""Async admission wrapper around the deliberately expensive Argon2 gateway."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

from core import auth as core_auth
from core.config import settings

if TYPE_CHECKING:
    from collections.abc import Callable

_gates: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = WeakKeyDictionary()


class AuthenticationCapacityError(RuntimeError):
    """No Argon2 worker slot is available for this authentication attempt."""


def _gate_for_running_loop() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    gate = _gates.get(loop)
    if gate is None:
        gate = asyncio.Semaphore(settings.auth.argon2_max_concurrency)
        _gates[loop] = gate
    return gate


def _release_slot(gate: asyncio.Semaphore, work: asyncio.Task[object]) -> None:
    """Release only when the worker really stopped, even if its caller cancelled."""
    gate.release()
    if not work.cancelled():
        # Retrieve a late exception when a cancelled HTTP request no longer awaits
        # the worker. Normal callers have already observed the same exception.
        work.exception()


async def _run_bounded(function: Callable[..., object], *args: str) -> object:
    gate = _gate_for_running_loop()
    # Admission is deliberately non-queuing. Argon2 is both CPU- and memory-hard;
    # an unbounded population of suspended login coroutines is itself a cheap DoS
    # and makes latency unknowable. There is no await between this check and the
    # acquire, so one asyncio loop cannot race another request into the same slot.
    if gate.locked():
        raise AuthenticationCapacityError
    await gate.acquire()
    try:
        work = asyncio.create_task(asyncio.to_thread(function, *args))
    except BaseException:
        gate.release()
        raise
    work.add_done_callback(partial(_release_slot, gate))
    # Shield the thread-backed task: cancelling a request cannot stop a running
    # native Argon2 call, so its capacity slot must remain occupied until it exits.
    return await asyncio.shield(work)


async def hash_password(password: str) -> str:
    return str(await _run_bounded(core_auth.hash_password, password))


async def verify_password(password: str, password_hash: str) -> bool:
    return bool(await _run_bounded(core_auth.verify_password, password, password_hash))


__all__ = ["AuthenticationCapacityError", "hash_password", "verify_password"]
