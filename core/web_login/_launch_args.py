"""The Chromium argv of an account's window: a pure function, so every flag can be pinned.

Split out of ``core.web_login.browser`` for its file-size budget (re-exported there).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from core.web_login.fingerprint import Fingerprint

# The relay's host. The one name Chrome may resolve itself: anything else is resolved by
# the account's proxy, never on this machine (an ISP's resolver would see every site).
RELAY_HOST = "127.0.0.1"
# Chrome's resolver rules: every name fails to resolve except the relay's. With an HTTP
# proxy Chrome sends the NAME in ``CONNECT host:port`` and the proxy resolves it, so this
# only ever fires on a request that would have bypassed the proxy.
HOST_RESOLVER_RULES = f"MAP * ~NOTFOUND , EXCLUDE {RELAY_HOST}"
# Features Chrome would use to talk to the network around ``--proxy-server``. Secure DNS
# (DoH) resolves names itself, over a direct HTTPS connection to the resolver. ONE
# ``--disable-features`` flag: a second one replaces the first.
_DISABLED_FEATURES = ("DnsOverHttps",)


def build_launch_args(
    *,
    user_data_dir: Path,
    relay_port: int,
    url: str,
    debug_port: int,
    fingerprint: Fingerprint,
) -> list[str]:
    """The Chromium argv: isolated profile, loopback proxy, WebRTC guard, identity, app mode.

    The DevTools endpoint is always emitted: the fingerprint is applied over it and
    dies with it. Its allow-origin is scoped to that exact loopback origin rather than
    the lifetime-wide ``*``.

    ``--user-agent`` / ``--lang`` are the NETWORK-layer half of the identity.
    ``Emulation.setUserAgentOverride`` is page-scoped and never reaches a browser-level
    shared worker, so without these the very connection whose ``initConnection`` claims
    a Mac would carry the operator's real Chrome/Windows request headers.

    Nothing but the relay is resolved or reached directly: ``--host-resolver-rules``
    fails every name but the relay's, Secure DNS is off, and no flag carries the
    proxy's credentials (the relay holds them).
    """
    return [
        f"--user-data-dir={user_data_dir}",
        f"--proxy-server=http://{RELAY_HOST}:{relay_port}",
        f"--host-resolver-rules={HOST_RESOLVER_RULES}",
        f"--disable-features={','.join(_DISABLED_FEATURES)}",
        # Keep WebRTC from leaking the real IP around the proxy. mDNS host-candidate
        # obfuscation stays ON: disabling it puts this desktop's LAN IP into the SDP of
        # every account's window — the same address in all of them.
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        f"--user-agent={fingerprint.user_agent}",
        f"--lang={fingerprint.locale}",
        f"--remote-debugging-port={debug_port}",
        # Recent Chrome refuses the CDP WebSocket without an allowed origin;
        # scope it to this endpoint instead of disabling the check with "*".
        f"--remote-allow-origins=http://{RELAY_HOST}:{debug_port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-service-autorun",
        "--disable-sync",
        # Component updates, the optimization guide, Safe Browsing update fetches and
        # domain reliability all go through --proxy-server, and each one opens a fresh
        # upstream tunnel — TCP + greeting + auth + CONNECT, ~600 ms at a residential
        # RTT — competing with WebK's own bundle exactly while time-to-signed-in is
        # being decided. The umbrella flag covers the lot and touches no page load.
        # It also keeps Google-domain background traffic off the account's exit IP.
        "--disable-background-networking",
        f"--app={url}",
    ]
