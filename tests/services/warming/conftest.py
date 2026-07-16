"""Shared isolation for warming service tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from services import warming
from services.warming import _runtime, _seams
from tests.services.warming._support import _ZERO_DELAY_FIELDS

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    # Gemini credentials live in .env (not the DB). Provide a non-empty default
    # so cycle/save tests see has_gemini_key=True without each one mucking with
    # the secret namespace.
    monkeypatch.setattr(settings.gemini, "api_key", "test-key")
    for field in _ZERO_DELAY_FIELDS:
        monkeypatch.setattr(settings.warming, field, 0.0)
    monkeypatch.setattr(settings.warming, "channels_per_cycle_min", 1)
    monkeypatch.setattr(settings.warming, "channels_per_cycle_max", 1)
    # Off-affinity exploration is a per-cycle RNG gate; neutralise it so a cycle
    # acts on its affinity slice deterministically (the dedicated tests re-enable
    # it). Without this the pinned rng.random→0.0 would fire exploration every cycle.
    monkeypatch.setattr(settings.warming, "channel_exploration_probability", 0.0)
    # Deterministic probability rolls: the reaction + persona-DM gates always
    # "pass" so tests exercise the real gates (reactions_enabled / dm_ok /
    # pending / cap), not the RNG. Tests that need a roll to *fail* override this.
    monkeypatch.setattr(_seams.rng, "random", lambda: 0.0)
    reset_logging_for_tests()
    setup_logging()
    warming._RUNTIME.clear()
    # _ACCOUNT_LOCKS are module-level and bound to the loop alive when created;
    # clear them too so each test gets fresh locks — needed when a runner like
    # mutmut drives several pytest sessions in one process (the loop changes).
    warming._ACCOUNT_LOCKS.clear()
    yield
    warming._RUNTIME.clear()
    warming._ACCOUNT_LOCKS.clear()
    # Abandon the periodic purge task (reconcile starts one) like the per-account
    # loops above, so it doesn't leak across tests.
    if _runtime._PURGE_TASK is not None:
        _runtime._PURGE_TASK.cancel()
        _runtime._PURGE_TASK = None
    reset_logging_for_tests()
