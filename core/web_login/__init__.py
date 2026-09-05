"""Web-login support: a loopback proxy relay plus a per-account signed-in browser.

Only what the service layer actually calls is re-exported. ``WebWindow``,
``Fingerprint``, ``build_launch_args`` and ``find_browser`` are types and seams the
launcher and its own tests import from their defining modules, so a package-level
alias for them was one more name to keep in sync for nobody.
"""

from __future__ import annotations

from core.web_login._page import (
    latest_login_token,
    page_state,
    release_capture,
    type_2fa_password,
)
from core.web_login.browser import (
    account_profile_dir,
    focus_window,
    launch_account_web,
    token_bytes,
)
from core.web_login.fingerprint import fingerprint_for
from core.web_login.relay import LocalProxyRelay

__all__ = [
    "LocalProxyRelay",
    "account_profile_dir",
    "fingerprint_for",
    "focus_window",
    "latest_login_token",
    "launch_account_web",
    "page_state",
    "release_capture",
    "token_bytes",
    "type_2fa_password",
]
