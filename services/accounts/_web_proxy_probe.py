"""The web-login proxy liveness probe: refuse a dead proxy, learn its country.

Split out of ``web_login`` for the file-size budget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.db import fetch_account_proxy_country
from core.proxy_check import PROXY_CHECK_TIMED_OUT, check_proxy_connectivity
from services.accounts._web_errors import _PROXY_DEAD_REFUSAL, WebLoginLaunchError

if TYPE_CHECKING:
    from schemas.proxy import ProxySettings

PROXY_PROBE_TIMEOUT = 5.0  # bounds the proxy probe's exit-IP fetch, never its geolocation
# A residential proxy's first request through a cold relay often needs longer than the
# first bound, so a TIMEOUT (never a refusal) is retried once with this one.
PROXY_PROBE_RETRY_TIMEOUT = 12.0


async def probe_exit_country(account_id: str, proxy: ProxySettings) -> str | None:
    """Refuse a dead proxy up front; return the country the identity is aligned to.

    The relay never touches the upstream, so a dead proxy otherwise failed only at the
    drive deadline as "not signed in". Once per new window. A stored country wins and
    skips geolocation; a fresh proxy gets the measured one, or None (claim no place).
    """
    stored = await fetch_account_proxy_country(account_id)
    result = await check_proxy_connectivity(
        proxy, exit_ip_timeout=PROXY_PROBE_TIMEOUT, geolocate=not stored
    )
    # Only a timeout is retried: a refused connection, bad credentials or a non-public
    # exit IP will not get better with a longer wait, so those stay dead at once.
    if result.status == "failed" and result.last_error == PROXY_CHECK_TIMED_OUT:
        result = await check_proxy_connectivity(
            proxy, exit_ip_timeout=PROXY_PROBE_RETRY_TIMEOUT, geolocate=not stored
        )
    if result.status == "failed":
        raise WebLoginLaunchError(_PROXY_DEAD_REFUSAL)
    return stored or result.country_code
