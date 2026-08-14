"""Generation fence for Telegram work performed by runtime-owned onboarding.

Direct/operator onboarding has no fence and keeps its historical behavior. The
background listener runtime installs a predicate for the duration of one campaign
pass. Every Telegram boundary checks it immediately before and after the await, so
a dependency that catches ``CancelledError`` cannot let a retired listener generation
continue with the next Telegram action.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


_CURRENT: ContextVar[Callable[[], bool] | None] = ContextVar(
    "neurocomment_onboarding_owner",
    default=None,
)


@contextmanager
def generation_fence(is_current: Callable[[], bool]) -> Iterator[None]:
    """Install the runtime ownership predicate in this task context."""
    token = _CURRENT.set(is_current)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def ensure_current() -> None:
    """Abort a retired runtime pass; no-op for direct onboarding calls."""
    is_current = _CURRENT.get()
    if is_current is not None and not is_current():
        raise asyncio.CancelledError
