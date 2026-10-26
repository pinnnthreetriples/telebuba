"""The web-login refusals: fixed, translatable codes and the errors that carry them.

Split out of ``web_login`` for the file-size budget; ``web_login`` re-exports them.
"""

from __future__ import annotations

import asyncio
import sys

# FIXED operator-facing refusals. ``str(exc)`` here would pipe arbitrary third-party
# text — Telethon's, binascii's, an OSError carrying an absolute host path — straight
# into a toast, so every refusal below picks one of these instead.
#
# CODES, not prose: the envelope's message is translated by the SPA through its
# ``shell.code.<code>`` tables (frontend/src/shared/i18n/{en,ru}.json) and an unknown
# string falls through verbatim — so English sentences here reach a Russian-first
# operator as English. Every code added must be added to BOTH tables.
_NO_PROXY_REFUSAL = "web_login_no_proxy"
_PROXY_DEAD_REFUSAL = "web_login_proxy_dead"
_LAUNCH_REFUSAL = "web_login_browser_failed"
_DRIVE_REFUSAL = "web_login_drive_failed"
_RELAY_REFUSAL = "web_login_relay_failed"
_SHUTDOWN_REFUSAL = "web_login_shutting_down"
# ``uvicorn --reload`` (and ``--workers N``) forces a Windows SelectorEventLoop, which
# implements no subprocess transport at all: ``create_subprocess_exec`` raises
# NotImplementedError. Nothing in the code can fix that, so say what to change.
_RELOAD_LOOP_REFUSAL = "web_login_reload_loop"


class WebLoginServiceError(ValueError):
    """Base for web-login refusals the operator should see (maps to a 400)."""


class NoProxyForWebLoginError(WebLoginServiceError):
    """The account has no proxy assigned, so no relay can front the browser."""


class WebLoginLaunchError(WebLoginServiceError):
    """The relay or browser could not be started (never carries proxy creds)."""


def _loop_cannot_spawn() -> bool:
    """True on the Windows SelectorEventLoop, whose subprocess transport is a stub.

    Only that loop earns the "restart without --reload" wording; a NotImplementedError
    from anywhere else would otherwise send the operator chasing a setting that is
    already right.
    """
    return sys.platform == "win32" and isinstance(
        asyncio.get_running_loop(), asyncio.SelectorEventLoop
    )
