"""The proxy watch: bound proxies only, one per pass, events without IPs."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from schemas.proxy import ProxyRead
from services.session_guard import _proxy_watch

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def _proxy(  # noqa: PLR0913 - one row's columns
    proxy_id: str,
    *,
    used: int = 1,
    status: str = "tcp_working",
    ip: str | None = "198.51.100.1",
    country: str | None = "DE",
    checked: datetime | None = None,
) -> ProxyRead:
    return ProxyRead.model_validate(
        {
            "id": proxy_id,
            "proxy_type": "socks5",
            "host": "proxy.example",
            "port": 1080,
            "has_password": False,
            "status": status,
            "exit_ip": ip,
            "country_code": country,
            "last_checked_at": checked.isoformat() if checked else None,
            "created_at": "2026-10-01T00:00:00+00:00",
            "updated_at": "2026-10-01T00:00:00+00:00",
            "used": used,
            "capacity": 5,
            "free": 4,
        }
    )


class _Wire:
    def __init__(self) -> None:
        self.proxies: list[ProxyRead] = []
        self.after: dict[str, ProxyRead] = {}
        self.checked: list[str] = []
        self.events: list[tuple[str, str, dict[str, object]]] = []
        self.fail = False


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> _Wire:
    state = _Wire()
    _proxy_watch.reset_for_tests()
    monkeypatch.setattr(_proxy_watch, "_rng", random.Random(3))  # noqa: S311 - seeded test draw

    async def _list() -> SimpleNamespace:
        if state.fail:
            raise RuntimeError
        return SimpleNamespace(proxies=state.proxies)

    async def _check(proxy_id: str) -> ProxyRead:
        state.checked.append(proxy_id)
        return state.after[proxy_id]

    async def _log(level: str, event: str, **kwargs: object) -> None:
        extra: dict[str, object] = dict(kwargs.get("extra") or {})  # ty: ignore[no-matching-overload]
        state.events.append((level, event, extra))

    monkeypatch.setattr(_proxy_watch, "list_proxies", _list)
    monkeypatch.setattr(_proxy_watch, "check_proxy", _check)
    monkeypatch.setattr(_proxy_watch, "log_event", _log)
    return state


@pytest.mark.asyncio
async def test_one_bound_proxy_per_pass_and_unbound_ones_never(wire: _Wire) -> None:
    wire.proxies = [_proxy("idle", used=0), _proxy("a"), _proxy("b")]
    wire.after = {"a": _proxy("a"), "b": _proxy("b")}

    first = await _proxy_watch.run_proxy_watch_pass(now=NOW)
    second = await _proxy_watch.run_proxy_watch_pass(now=NOW)
    third = await _proxy_watch.run_proxy_watch_pass(now=NOW)

    assert (first, second, third) == ("a", "b", None)
    assert wire.checked == ["a", "b"]


@pytest.mark.asyncio
async def test_a_proxy_is_due_again_after_the_interval_with_jitter(wire: _Wire) -> None:
    wire.proxies = [_proxy("a")]
    wire.after = {"a": _proxy("a")}
    await _proxy_watch.run_proxy_watch_pass(now=NOW)

    due = _proxy_watch._due["a"] - NOW
    assert timedelta(hours=6) <= due <= timedelta(hours=6.6)
    assert await _proxy_watch.run_proxy_watch_pass(now=NOW + timedelta(hours=5)) is None
    assert await _proxy_watch.run_proxy_watch_pass(now=NOW + timedelta(hours=7)) == "a"


@pytest.mark.asyncio
async def test_the_stored_check_time_seeds_the_schedule(wire: _Wire) -> None:
    wire.proxies = [_proxy("a", checked=NOW - timedelta(hours=1))]
    wire.after = {"a": _proxy("a")}

    assert await _proxy_watch.run_proxy_watch_pass(now=NOW) is None


@pytest.mark.asyncio
async def test_zero_hours_switches_the_watch_off(
    wire: _Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_proxy_watch.settings.session_audit, "proxy_watch_hours", 0.0)
    wire.proxies = [_proxy("a")]

    assert await _proxy_watch.run_proxy_watch_pass(now=NOW) is None
    assert wire.checked == []


@pytest.mark.asyncio
async def test_a_proxy_that_started_failing_is_a_warning_with_the_account_count(
    wire: _Wire,
) -> None:
    wire.proxies = [_proxy("a", used=3)]
    wire.after = {"a": _proxy("a", used=3, status="failed", ip=None, country=None)}

    await _proxy_watch.run_proxy_watch_pass(now=NOW)

    assert wire.events == [("WARNING", "proxy_check_failed", {"proxy_id": "a", "accounts": 3})]


@pytest.mark.asyncio
async def test_a_proxy_that_was_already_failing_is_not_reported_again(wire: _Wire) -> None:
    wire.proxies = [_proxy("a", status="failed")]
    wire.after = {"a": _proxy("a", status="failed")}

    await _proxy_watch.run_proxy_watch_pass(now=NOW)

    assert wire.events == []


@pytest.mark.asyncio
async def test_a_moved_country_is_a_warning_and_only_the_ip_is_info_without_any_ip(
    wire: _Wire,
) -> None:
    wire.proxies = [_proxy("a"), _proxy("b")]
    wire.after = {
        "a": _proxy("a", country="NL", ip="203.0.113.9"),
        "b": _proxy("b", ip="203.0.113.10"),
    }

    await _proxy_watch.run_proxy_watch_pass(now=NOW)
    await _proxy_watch.run_proxy_watch_pass(now=NOW)

    assert wire.events == [
        ("WARNING", "proxy_exit_changed", {"proxy_id": "a", "accounts": 1, "what": "country"}),
        ("INFO", "proxy_exit_changed", {"proxy_id": "b", "accounts": 1, "what": "ip"}),
    ]
    assert "203.0.113" not in repr(wire.events)


@pytest.mark.asyncio
async def test_an_unknown_country_on_either_side_is_not_a_country_change(wire: _Wire) -> None:
    wire.proxies = [_proxy("a", country=None)]
    wire.after = {"a": _proxy("a", country="NL")}

    await _proxy_watch.run_proxy_watch_pass(now=NOW)

    assert wire.events == []


@pytest.mark.asyncio
async def test_a_failing_read_or_check_never_raises(wire: _Wire) -> None:
    wire.fail = True
    assert await _proxy_watch.run_proxy_watch_pass(now=NOW) is None

    wire.fail = False
    wire.proxies = [_proxy("a")]
    wire.after = {}  # the check raises KeyError
    assert await _proxy_watch.run_proxy_watch_pass(now=NOW) is None
