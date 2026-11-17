"""Where an account's web window may be pointed, by name, and how a raised window gets there.

Split out of ``web_login`` for the file-size budget. A target is a fixed word chosen in code (never
an address from a request): ``spambot`` is @SpamBot's dialog in WebK, for the operator's own appeal
(docs/spam-intel.md 5.12). Opening it is one navigation: nothing is clicked or typed.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Literal

from core.config import settings
from core.web_login import navigate_window, spambot_dialog_url
from core.web_login._cdp import CdpError

if TYPE_CHECKING:
    from core.web_login.browser import WebWindow

WebTarget = Literal["spambot"]


def target_url(target: WebTarget | None) -> str | None:
    """The address a named target opens (``None`` = WebK's own start page)."""
    if target is None:
        return None
    return spambot_dialog_url(settings.spam_intel.appeal_dialog_target)


async def navigate_quietly(window: WebWindow, url: str) -> None:
    """Point the raised window at ``url``; a window that cannot be navigated stays as it is.

    The window is already up and signed in, which is what the operator asked for: if the
    navigation fails (the renderer died), the operator still has WebK and searches by hand.
    """
    with suppress(CdpError, OSError):
        await navigate_window(window, url)
