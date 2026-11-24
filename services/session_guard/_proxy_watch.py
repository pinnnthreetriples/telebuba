"""The proxy watch: every proxy an account depends on, checked in the background.

``services.proxies.check_proxy`` ran only when the operator pressed the button, so a proxy
that died, or whose exit moved to another country, was noticed when an account's action
failed. One proxy per guard pass, each every ``SESSION_AUDIT__PROXY_WATCH_HOURS`` (0 disables)
with a little jitter, and only proxies bound to at least one account. The due time is in
memory (one uvicorn worker), seeded from the stored ``last_checked_at``.

Journalled: ``proxy_check_failed`` (it became ``failed``), ``proxy_exit_changed`` with
``what: country`` (WARNING: the accounts behind it now appear in another country) or
``what: ip`` (INFO: rotating proxies do that normally). Never an IP, a host or a credential.
Nothing here raises.
"""

from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import list_proxies
from core.logging import log_event
from services.proxies import check_proxy

if TYPE_CHECKING:
    from schemas.proxy import ProxyRead

logger = logging.getLogger(__name__)

# Seam for tests; only the jitter is drawn.
_rng = random.Random()  # nosec B311  # noqa: S311 - pacing jitter, not security

_JITTER = 0.1
_due: dict[str, datetime] = {}
# The last exit country this process saw for each proxy (a failed check stores none).
_last_country: dict[str, str] = {}


def _interval() -> timedelta:
    hours = settings.session_audit.proxy_watch_hours
    return timedelta(hours=hours * (1 + _rng.uniform(0.0, _JITTER)))


def _due_at(proxy: ProxyRead, now: datetime) -> datetime:
    known = _due.get(proxy.id)
    if known is None:
        last = proxy.last_checked_at
        known = now if last is None else datetime.fromisoformat(last) + _interval()
        _due[proxy.id] = known
    return known


async def _journal(before: ProxyRead, after: ProxyRead) -> None:
    extra: dict[str, object] = {"proxy_id": after.id, "accounts": after.used}
    if before.country_code:
        _last_country.setdefault(after.id, before.country_code)
    if after.status == "failed" and before.status != "failed":
        await log_event("WARNING", "proxy_check_failed", extra=extra)
        return
    # A failed check stores no country: the last one this process saw bridges the outage.
    known = before.country_code or _last_country.get(after.id)
    if after.country_code:
        _last_country[after.id] = after.country_code
    country_moved = bool(known and after.country_code and known != after.country_code)
    if country_moved:
        await log_event("WARNING", "proxy_exit_changed", extra={**extra, "what": "country"})
    elif before.exit_ip and after.exit_ip and before.exit_ip != after.exit_ip:
        await log_event("INFO", "proxy_exit_changed", extra={**extra, "what": "ip"})


async def _run(now: datetime) -> str | None:
    if settings.session_audit.proxy_watch_hours <= 0:
        return None
    bound = [proxy for proxy in (await list_proxies()).proxies if proxy.used > 0]
    ready = [(due, proxy) for proxy in bound if (due := _due_at(proxy, now)) <= now]
    if not ready:
        return None
    before = min(ready, key=lambda item: item[0])[1]
    _due[before.id] = now + _interval()
    await _journal(before, await check_proxy(before.id))
    return before.id


async def run_proxy_watch_pass(*, now: datetime | None = None) -> str | None:
    """Check the most overdue bound proxy, if one is due; its id, or ``None``. Never raises."""
    try:
        return await _run(now or datetime.now(UTC))
    except Exception:  # noqa: BLE001 - a background watch must never end the guard step
        logger.warning("proxy watch failed", exc_info=True)
        return None


def reset_for_tests() -> None:
    """Forget the in-memory schedule between tests."""
    _due.clear()
    _last_country.clear()
