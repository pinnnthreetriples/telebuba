"""Warming tests split from the former service test module: test_readiness.py."""

from __future__ import annotations

from services import warming
from tests.services.warming._support import (
    _account,
)


def test_evaluate_readiness_ready() -> None:
    account = _account(proxy_host="1.2.3.4", proxy_status="tcp_working")
    readiness = warming.evaluate_readiness(account, 3)
    assert readiness.ready is True
    assert readiness.reasons == []


def test_evaluate_readiness_collects_all_blockers() -> None:
    account = _account(status="new")  # no proxy, no channels
    readiness = warming.evaluate_readiness(account, 0)
    assert readiness.ready is False
    assert any("session" in reason for reason in readiness.reasons)
    assert "no proxy" in readiness.reasons
    assert "no channels" in readiness.reasons


def test_evaluate_readiness_flags_failed_proxy() -> None:
    account = _account(proxy_host="1.2.3.4", proxy_status="failed")
    readiness = warming.evaluate_readiness(account, 1)
    assert "proxy failed" in readiness.reasons


def test_proxy_snapshot_none_without_proxy() -> None:
    assert warming._proxy_snapshot(_account()) is None


def test_proxy_snapshot_formats_with_country() -> None:
    account = _account(
        proxy_type="socks5",
        proxy_host="1.2.3.4",
        proxy_port=1080,
        proxy_country_code="US",
    )
    assert warming._proxy_snapshot(account) == "socks5://1.2.3.4:1080 (US)"
