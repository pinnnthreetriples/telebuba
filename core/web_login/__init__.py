"""Web-login support: a loopback proxy relay plus a per-account signed-in browser."""

from __future__ import annotations

from core.web_login.browser import (
    account_profile_dir,
    build_launch_args,
    find_browser,
    open_account_web,
    relaunch_account_web,
)
from core.web_login.relay import LocalProxyRelay
from core.web_login.storage import build_webk_localstorage

__all__ = [
    "LocalProxyRelay",
    "account_profile_dir",
    "build_launch_args",
    "build_webk_localstorage",
    "find_browser",
    "open_account_web",
    "relaunch_account_web",
]
