"""Web-login support: a loopback proxy relay plus a per-account signed-in browser."""

from __future__ import annotations

from core.web_login.browser import (
    account_profile_dir,
    build_launch_args,
    find_browser,
    latest_login_token,
    launch_webk_with_hook,
    page_state,
    relaunch_account_web,
    token_bytes,
    type_2fa_password,
)
from core.web_login.relay import LocalProxyRelay

__all__ = [
    "LocalProxyRelay",
    "account_profile_dir",
    "build_launch_args",
    "find_browser",
    "latest_login_token",
    "launch_webk_with_hook",
    "page_state",
    "relaunch_account_web",
    "token_bytes",
    "type_2fa_password",
]
