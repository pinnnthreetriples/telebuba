"""Probes of the window's Chromium argv: nothing re-enables DNS or carries a credential."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.web_login._launch_args import build_launch_args
from tests.core.web_login_helpers import FINGERPRINT


def _args(**over: object) -> list[str]:
    base: dict[str, object] = {
        "user_data_dir": Path(r"C:\profiles\acct-1"),
        "relay_port": 41000,
        "debug_port": 42000,
        "url": "about:blank",
        "fingerprint": FINGERPRINT,
    }
    return build_launch_args(**{**base, **over})  # ty: ignore[invalid-argument-type]


def test_nothing_re_enables_a_resolver_or_a_direct_path() -> None:
    joined = " ".join(_args())
    for forbidden in (
        "--enable-features",
        "--dns-over-https",
        "--enable-quic",
        "--host-rules",
        "--no-proxy-server",
        "--proxy-bypass-list",
        "--proxy-pac-url",
        "--enable-async-dns",
    ):
        assert forbidden not in joined


def test_each_flag_appears_once() -> None:
    names = [arg.split("=", 1)[0] for arg in _args()]
    assert len(names) == len(set(names))


def test_no_flag_carries_a_credential_whatever_the_relay_port() -> None:
    for arg in _args(relay_port=1):
        assert "@" not in arg.split("=", 1)[-1] or arg.startswith(("--user-agent", "--app"))
        assert "password" not in arg.lower()
        assert "socks" not in arg.lower()


@pytest.mark.parametrize("url", ["about:blank", "https://web.telegram.org/k/"])
def test_the_resolver_rule_is_the_same_for_every_start_url(url: str) -> None:
    rules = [a for a in _args(url=url) if a.startswith("--host-resolver-rules=")]
    assert rules == ["--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1"]
