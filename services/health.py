"""Readiness — whether the dependencies the API needs are actually reachable.

Distinct from liveness (``GET /health``), which answers "this process is up" and
touches no I/O. A liveness probe stays green while the datastore is missing,
locked, or corrupt, so it cannot be used to decide whether to send traffic here.

Failures go to the STDLIB logger, not ``log_event``: ``log_event`` persists into
the very SQLite file this check just found unreachable, and the probe is
unauthenticated and polled on an interval, so routing it through the ``logs``
table would make an outage a write amplifier.

For the same reason this is the one stdlib sink in the repo that logs the type
INSTEAD of the full text, where ``api.errors`` and ``core.proxy_check`` log the
text. Those are reached through an authenticated route or an operator action;
this one an outsider can call in a loop, and the repo's only rate limiter is on
login. ``logger.exception`` would attach ``exc_info``, and SQLAlchemy renders the
failing SQL and the datastore path into it — so during any outage a stranger
could drive both unbounded growth of ``debug.log`` and, through Sentry's default
``LoggingIntegration``, quota burn on records carrying that path.
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
    except Exception as exc:  # noqa: BLE001 — a probe reports, it never propagates.
        # Type name only, and NO ``exc_info`` — see the module docstring. SQLAlchemy's
        # ``StatementError`` renders the SQL and its bound parameters, which for this
        # datastore includes its path, and an unauthenticated caller decides how often
        # this line is written.
        logger.error("readiness: database unreachable (%s)", type(exc).__name__)  # noqa: TRY400
        return ReadinessStatus(status="unavailable", database=False)
    return ReadinessStatus(status="ok", database=True)
