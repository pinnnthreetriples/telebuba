"""Per-key async locks that serialize concurrent same-key account imports.

Two imports racing on the same session name / account_id both pass the
check-then-write preflight and the second silently overwrites the first (and a
failing import's rollback can delete a concurrent success's row/file). Holding
one lock per key across the whole check→write→add (→rollback) sequence makes
same-key imports serialize: the second observes the now-existing account and
raises the normal "already exists" error instead of clobbering.

Mirrors the per-account lock in ``services.warming._runtime`` — a lazily created
dict entry per key, never freed (bounded by the number of distinct import keys).
"""

from __future__ import annotations

import asyncio

_IMPORT_LOCKS: dict[str, asyncio.Lock] = {}


def import_lock(key: str) -> asyncio.Lock:
    """Public accessor for the per-key import lock (created lazily)."""
    lock = _IMPORT_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _IMPORT_LOCKS[key] = lock
    return lock
