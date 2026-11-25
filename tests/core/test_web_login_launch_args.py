"""The window's Chromium argv keeps every name and every byte on the account's proxy.

Pure function, no browser: the resolver rule, Secure DNS and the proxy flag are pinned
here because a regression in any of them sends a lookup or a connection out of this
machine, around the account's exit IP.
"""

from __future__ import annotations

from pathlib import Path

from core.web_login import browser
from core.web_login._launch_args import HOST_RESOLVER_RULES, RELAY_HOST, build_launch_args
from tests.core.web_login_helpers import FINGERPRINT

_RELAY_PORT = 41000


def _args() -> list[str]:
    return build_launch_args(
        user_data_dir=Path(r"C:\profiles\acct-1"),
        relay_port=_RELAY_PORT,
        debug_port=42000,
        url="about:blank",
        fingerprint=FINGERPRINT,
    )


def _value(args: list[str], flag: str) -> str:
    (found,) = [arg for arg in args if arg.startswith(f"{flag}=")]
    return found.removeprefix(f"{flag}=")


def test_the_browser_module_serves_the_same_builder() -> None:
    assert browser.build_launch_args is build_launch_args


def test_no_name_is_resolved_on_this_machine_but_the_relays() -> None:
    rules = _value(_args(), "--host-resolver-rules")

    assert rules == HOST_RESOLVER_RULES
    mapped, excluded = (part.strip() for part in rules.split(","))
    assert mapped == "MAP * ~NOTFOUND"
    assert excluded == f"EXCLUDE {RELAY_HOST}"
    assert RELAY_HOST == "127.0.0.1"


def test_secure_dns_is_off_in_the_one_disable_features_flag() -> None:
    args = _args()

    # A second ``--disable-features`` replaces the first, so there is exactly one.
    assert len([arg for arg in args if arg.startswith("--disable-features")]) == 1
    assert "DnsOverHttps" in _value(args, "--disable-features").split(",")


def test_the_proxy_flag_still_points_at_the_relay() -> None:
    args = _args()

    assert _value(args, "--proxy-server") == f"http://{RELAY_HOST}:{_RELAY_PORT}"
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args


def test_no_flag_carries_proxy_credentials() -> None:
    proxy = _value(_args(), "--proxy-server")

    assert "@" not in proxy
    assert not any(arg.startswith(("--proxy-auth", "--proxy-user")) for arg in _args())
    # Only the relay's port is known to the builder: there is nothing else to leak.
    assert proxy.removeprefix("http://") == f"{RELAY_HOST}:{_RELAY_PORT}"
