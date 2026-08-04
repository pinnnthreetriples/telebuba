"""Readiness — whether the dependencies the API needs are actually reachable.

Distinct from liveness (``GET /health``), which answers "this process is up" and
touches no I/O. A liveness probe stays green while the datastore is missing,
locked, or corrupt, so it cannot be used to decide whether to send traffic here.

Failures go to the STDLIB logger, not ``log_event``: ``log_event`` persists into
the very SQLite file this check just found unreachable, and the probe is
unauthenticated and polled on an interval, so routing it through the ``logs``
table would make an outage a write amplifier. Same stdlib-sink pattern as
``api.errors`` and ``core.proxy_check``.
"""

from __future__ import annotations

import logging

from core.db import check_database_reachable
from schemas.api import ReadinessStatus

logger = logging.getLogger(__name__)

__all__ = ["check_readiness"]


async def check_readiness() -> ReadinessStatus:
    """Report per-dependency reachability, leaking nothing about the failure."""
    try:
        await check_database_reachable()
    except Exception as exc:
        # Type name only. SQLAlchemy's ``StatementError.__str__`` appends the SQL
        # and its bound parameters — for this datastore that includes its path —
        # so the full text stays with ``logger.exception`` below and never becomes
        # part of a message an unauthenticated caller could provoke.
        logger.exception("readiness: database unreachable (%s)", type(exc).__name__)
        return ReadinessStatus(status="unavailable", database=False)
    return ReadinessStatus(status="ok", database=True)
