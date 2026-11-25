"""Isolation for the custody matrix: a fresh process's module state before and after each test."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.telegram_client import _auth, _pool
from services.accounts import _key_repair, _twofa_pending, session_rekey

if TYPE_CHECKING:
    from collections.abc import Iterator


def _fresh_process() -> None:
    """What a restart resets: the repair's retry schedule, the pool, the auth locks."""
    _key_repair.reset_for_tests()
    session_rekey.reset_for_tests()
    _pool._reset_for_tests()
    _auth._AUTH_LOCKS.clear()
    _twofa_pending._UNSETTLED_AT.clear()


@pytest.fixture(autouse=True)
def _restart_state() -> Iterator[None]:
    _fresh_process()
    yield
    _fresh_process()
