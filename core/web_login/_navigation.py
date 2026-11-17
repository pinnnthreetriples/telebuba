"""Where an open window goes: WebK's start page, @SpamBot's dialog, and the two CDP commands.

Two commands only, both on a window somebody else launched: raise it (``Page.bringToFront``) and
point it at an address (``Page.navigate``). Nothing here clicks, types or evaluates a script; the
semi-automatic appeal (docs/spam-intel.md 5.12) is the operator's own hand in the window, and
``tests/services/spam_intel/test_appeal_hard_limit.py`` pins that these stay the only commands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.web_login.browser import WebWindow

WEBK_URL = "https://web.telegram.org/k/"

# Where a window opens on @SpamBot's dialog, by the name ``SPAM_INTEL__APPEAL_DIALOG_TARGET``
# gives. ``hash`` is WebK's own deep link (it resolves ``#@username`` itself); ``tgaddr`` is the
# form Telegram's ``tg://`` links use on the web. NEITHER is verified against the installed WebK
# build (a live check on one account the owner chooses): which one is used is a setting, and if the
# dialog does not open the window is still on WebK and the operator finds the bot by hand.
_SPAMBOT_DIALOG_URLS = {
    "hash": f"{WEBK_URL}#@SpamBot",
    "tgaddr": f"{WEBK_URL}?tgaddr=tg%3A%2F%2Fresolve%3Fdomain%3DSpamBot",
}
_DEFAULT_DIALOG_TARGET = "hash"


def spambot_dialog_url(target: str) -> str:
    """The address that opens @SpamBot's dialog in WebK, for the named candidate."""
    return _SPAMBOT_DIALOG_URLS.get(target, _SPAMBOT_DIALOG_URLS[_DEFAULT_DIALOG_TARGET])


async def focus_window(window: WebWindow) -> None:
    """Raise an already-open window instead of launching a second one."""
    await window.session.send_command("Page.bringToFront", session_id=window.page)


async def navigate_window(window: WebWindow, url: str) -> None:
    """Point an open window at ``url``: one ``Page.navigate``, nothing is clicked or typed."""
    await window.session.send_command("Page.navigate", {"url": url}, session_id=window.page)
