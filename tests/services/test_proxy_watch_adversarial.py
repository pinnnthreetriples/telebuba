"""A country change that happens across an outage must still be reported."""

from __future__ import annotations

from datetime import timedelta

import pytest

from services.session_guard import _proxy_watch
from tests.services.test_proxy_watch import NOW, _proxy, _Wire, wire  # noqa: F401


@pytest.mark.asyncio
async def test_country_change_across_a_failed_check_is_reported(wire: _Wire) -> None:  # noqa: F811
    wire.proxies = [_proxy("a", country="DE")]
    wire.after = {"a": _proxy("a", status="failed", ip=None, country=None)}
    await _proxy_watch.run_proxy_watch_pass(now=NOW)
    # The database now holds what the failed check wrote; the proxy comes back from Poland.
    wire.proxies = [wire.after["a"]]
    wire.after = {"a": _proxy("a", country="PL", ip="203.0.113.9")}
    later = NOW + timedelta(days=3)
    await _proxy_watch.run_proxy_watch_pass(now=later)
    assert ("WARNING", "proxy_exit_changed") in [(lvl, ev) for lvl, ev, _ in wire.events]
