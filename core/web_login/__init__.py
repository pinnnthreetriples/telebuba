"""Web-login support: a loopback proxy relay plus a per-account signed-in browser."""

from __future__ import annotations

from core.web_login.browser import (
    WebWindow,
    account_profile_dir,
    build_launch_args,
    find_browser,
    focus_window,
    latest_login_token,
    launch_account_web,
    page_state,
    token_bytes,
    type_2fa_password,
)
from core.web_login.fingerprint import Fingerprint, fingerprint_for
from core.web_login.relay import LocalProxyRelay

__all__ = [
    "Fingerprint",
    "LocalProxyRelay",
    "WebWindow",
    "account_profile_dir",
    "build_launch_args",
    "find_browser",
    "fingerprint_for",
    "focus_window",
    "latest_login_token",
    "launch_account_web",
    "page_state",
    "token_bytes",
    "type_2fa_password",
]
