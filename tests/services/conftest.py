"""Shared isolation for service tests that touch process-global caches."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from services.accounts import profile_read

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _reset_profile_read_state() -> Iterator[None]:
    """Give every test the module state a fresh process would have.

    ``invalidate_account_profile_cache`` is production invalidation, not a
    reset: it BUMPS ``_CACHE_GEN`` per account and leaves the key in place. Used
    as test isolation it left ``{"acc-1": 1}`` behind, so the next test never
    exercised the ``.get(account_id, 0)`` defaults — every mutation of those
    defaults read as equivalent and survived. Which mutants that hid depended on
    what ran earlier in the same worker process, which is why they flipped
    between Nightly sweeps.
    """
    for state in (profile_read._CACHE, profile_read._CACHE_GEN, profile_read._INFLIGHT):
        state.clear()
    yield
    for state in (profile_read._CACHE, profile_read._CACHE_GEN, profile_read._INFLIGHT):
        state.clear()
