"""The web-login liveness probe: a timeout is retried once, a hard failure is not."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from core.proxy_check import PROXY_CHECK_TIMED_OUT
from schemas.proxy import ProxyCheckResult
from services.accounts import _web_proxy_probe
from services.accounts.web_login import WebLoginLaunchError, open_account_web
from tests.services.accounts._web_login_support import PROXY as _PROXY
from tests.services.accounts._web_login_support import fresh_registry, relay
from tests.services.accounts._web_login_support import patch_profile as _patch_profile
from tests.services.accounts._web_login_support import patch_proxy as _patch_proxy
from tests.services.accounts._web_login_support import wire as _wire

if TYPE_CHECKING:
    from pathlib import Path

    from tests.services.accounts._web_login_support import FakeRelay as _FakeRelay

__all__ = ["fresh_registry", "relay"]

_TIMEOUT = ProxyCheckResult(status="failed", last_error=PROXY_CHECK_TIMED_OUT)
_REFUSED = ProxyCheckResult(status="failed", last_error="ConnectionRefusedError")
_ALIVE = ProxyCheckResult(status="tcp_working", country_code="DE")


def _script(monkeypatch: pytest.MonkeyPatch, *answers: ProxyCheckResult) -> list[float | None]:
    """Answer the probe from ``answers`` in order; return the exit-IP timeouts it was given."""
    timeouts: list[float | None] = []
    pending = list(answers)

    async def _check(_proxy: object, *, exit_ip_timeout: float | None = None, **_: object):
        timeouts.append(exit_ip_timeout)
        return pending.pop(0)

    monkeypatch.setattr(_web_proxy_probe, "check_proxy_connectivity", _check)
    return timeouts


@pytest.mark.asyncio
async def test_a_timed_out_probe_is_retried_longer_and_the_window_opens(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],  # noqa: ARG001 - the launch needs a relay port
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    timeouts = _script(monkeypatch, _TIMEOUT, _ALIVE)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["qr", "logged_in"])

    await open_account_web("acct")

    assert timeouts == [5.0, 12.0]
    assert calls["launches"] == 1


@pytest.mark.asyncio
async def test_two_timeouts_leave_the_proxy_dead(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    timeouts = _script(monkeypatch, _TIMEOUT, _TIMEOUT)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["qr"])

    with pytest.raises(WebLoginLaunchError) as caught:
        await open_account_web("acct")

    assert str(caught.value) == "web_login_proxy_dead"
    assert timeouts == [5.0, 12.0]
    assert relay.created == []


@pytest.mark.asyncio
async def test_a_hard_failure_is_dead_at_once_with_no_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    timeouts = _script(monkeypatch, _REFUSED)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["qr"])

    with pytest.raises(WebLoginLaunchError) as caught:
        await open_account_web("acct")

    assert str(caught.value) == "web_login_proxy_dead"
    assert timeouts == [5.0]
    assert relay.created == []
