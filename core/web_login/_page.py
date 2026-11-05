"""In-page probes and real input for an open WebK window.

Split out of ``browser`` so that module can stay about launching and owning the
process; everything here talks to a page that is already up, over the window's
browser-level CDP socket. The expressions are byte-for-byte the ones verified
live against a real WebK build — see the comments on each for why they read
structure rather than words.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import TYPE_CHECKING

from core.web_login._cdp import CdpError

if TYPE_CHECKING:
    from collections.abc import Callable

    from core.web_login.browser import WebWindow

# In-page probes: read the captured tokens, and classify the visible page.
_READ_CAPTURED_EXPR = "JSON.stringify(window.__cap||[])"
# Fires the QR hook's own teardown. The hook exposes it and never fires it itself: no
# in-page test can tell that the login is over. WebK's chat-shell markers sit in the
# document, zero-sized, from the first paint — a script watching for them drops the
# token array while the QR screen is still up, and then no token is ever captured.
# ``page_state`` is what actually knows the safe moment, and it runs here.
_RELEASE_CAPTURE_EXPR = "typeof window.__capOff==='function'?(window.__capOff(),1):0"
# STRUCTURE, never words: an account's locale follows its proxy country and most of
# them are not English, so a text probe matches nothing on a German QR screen, reports
# it as logged in, and strands the profile (marked signed-in) on that screen for good.
# Elements instead: WebK's auth container, the canvas it paints the QR into, a VISIBLE
# password field, the chat shell it swaps in. None of that is translated.
_PAGE_STATE_EXPR = (
    "(()=>{const vis=e=>{const r=e.getBoundingClientRect();"
    "return r.width>0&&r.height>0;};"
    "const sel=s=>[...document.querySelectorAll(s)].some(vis);"
    "return JSON.stringify({"
    "auth: sel('#auth-pages'),"
    "pw: sel('input[type=password]'),"
    "qr: [...document.querySelectorAll('#auth-pages canvas,canvas.qr-canvas')]"
    ".some(c=>{const r=c.getBoundingClientRect();return r.width>=100&&r.height>=100;}),"
    "app: sel('#column-center,.chatlist')});})()"
)
# WebK's controlled password input only accepts typed characters after a REAL mouse
# click (a JS ``.focus()`` leaves its React state empty, so the submit sends a blank
# password), and it renders a hidden decoy ``input[type=password]`` (zero height) next
# to the visible one. So: the VISIBLE password input, never merely "a wide input",
# which would type the cloud password into whatever field WebK renders first.
_INPUT_RECT_EXPR = (
    "(()=>{const i=[...document.querySelectorAll('input[type=password]')]"
    ".find(i=>{const r=i.getBoundingClientRect();return r.width>200&&r.height>0;});"
    "if(!i)return '';const r=i.getBoundingClientRect();"
    "return JSON.stringify({x:r.x+r.width/2,y:r.y+r.height/2});})()"
)
# The visible password field's character count: only a length ever leaves the page.
_FIELD_LENGTH_EXPR = (
    "(()=>{const i=[...document.querySelectorAll('input[type=password]')]"
    ".find(i=>{const r=i.getBoundingClientRect();return r.width>200&&r.height>0;});"
    "return i?i.value.length:null;})()"
)
# What WebK shows after a password submit, from STRUCTURE like everything here: an error
# class on the field's wrapper (the wrong-password state), or a busy control (a disabled
# button / a preloader) while ``auth.checkPassword`` is still running. The selectors
# follow WebK's class names, not its words, so they hold in every locale.
_SUBMIT_OUTCOME_EXPR = (
    "(()=>{const vis=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0;};"
    "const any=s=>[...document.querySelectorAll('#auth-pages '+s)].some(vis);"
    "if(any('.input-field.error')||any('.input-field-error')||any('.is-error'))return 'error';"
    "if(any('button[disabled]')||any('.preloader')||any('.btn-primary.is-loading')"
    "||any('.btn-primary.disabled'))return 'checking';"
    "return 'idle';})()"
)
_CLICK_SETTLE_SECONDS = 0.2
# Reads of the field's position before it is trusted to have stopped sliding in, and the
# gap between them.
_SETTLE_READS = 8
_SETTLE_READ_GAP_SECONDS = 0.15
# Typing the password again when the field did not take it, before giving up on a submit.
_TYPE_ATTEMPTS = 2
# CDP real-key typing gap between characters, so WebK's field handlers keep up.
_KEY_GAP_SECONDS = 0.02
_CTRL_MODIFIER = 2
# WebK submits the password form on Enter, and Enter is the same key in every language:
# a button hunted by its label ("Next", "Weiter", ...) is never found on most of the
# locales we hand out, and the typed password would sit there unsubmitted.
_ENTER_KEY: dict[str, object] = {
    "key": "Enter",
    "code": "Enter",
    "windowsVirtualKeyCode": 13,
    "text": "\r",
}


def _evaluate_value(response: dict) -> object:
    """Pull ``result.result.value`` off a Runtime.evaluate response, or ``None``."""
    try:
        return response["result"]["result"]["value"]
    except (KeyError, TypeError):
        return None


async def _evaluate(window: WebWindow, expression: str) -> object:
    response = await window.session.send_command(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True},
        session_id=window.page,
    )
    return _evaluate_value(response)


async def latest_login_token(window: WebWindow) -> str | None:
    """The freshest ``auth.loginToken`` the hook captured (base64url), or ``None``."""
    value = await _evaluate(window, _READ_CAPTURED_EXPR)
    if not isinstance(value, str):
        return None
    try:
        tokens = json.loads(value)
    except ValueError:
        return None
    return tokens[-1] if tokens else None


async def release_capture(window: WebWindow) -> None:
    """Drop the captured login tokens and undo the hook's patches. After login only.

    Best effort by design: the tokens are a hygiene concern, not a correctness one, and
    a window that has just logged in must never be failed because tidying it up did not
    answer. A window without the hook (a repeat open) simply has nothing to release.
    """
    with suppress(Exception):
        await _evaluate(window, _RELEASE_CAPTURE_EXPR)


async def page_state(window: WebWindow) -> str:
    """Classify the WebK page as ``password`` / ``qr`` / ``logged_in`` / ``loading``."""
    value = await _evaluate(window, _PAGE_STATE_EXPR)
    if not isinstance(value, str):
        return "loading"
    try:
        info = json.loads(value)
    except ValueError:
        return "loading"
    if info.get("pw"):
        return "password"
    # The auth screen without a password field is the QR screen (the login we drive).
    if info.get("qr") or info.get("auth"):
        return "qr"
    # Only the shell counts as logged in, and it is only reached here once the auth
    # screen is gone: never "the page has some text on it", which every locale passes.
    if info.get("app"):
        return "logged_in"
    return "loading"


async def _dispatch_key(window: WebWindow, event: dict[str, object]) -> None:
    await window.session.send_command("Input.dispatchKeyEvent", event, session_id=window.page)


async def _click_point(window: WebWindow, point: dict[str, float]) -> None:
    for kind in ("mousePressed", "mouseReleased"):
        await window.session.send_command(
            "Input.dispatchMouseEvent",
            {
                "type": kind,
                "x": point["x"],
                "y": point["y"],
                "button": "left",
                "clickCount": 1,
                "buttons": 1,
            },
            session_id=window.page,
        )


async def _locate(window: WebWindow, rect_expr: str) -> dict[str, float] | None:
    """The centre point ``rect_expr`` reports, or ``None`` when the element is not there."""
    value = await _evaluate(window, rect_expr)
    if not isinstance(value, str) or not value:
        return None
    try:
        point = json.loads(value)
    except ValueError:
        return None
    return point if isinstance(point, dict) else None


async def _settled_input_point(window: WebWindow) -> dict[str, float] | None:
    """The password field's centre once two reads agree; ``None`` when there is no field.

    WebK slides the password page in, and a click aimed at the first width-passing rect
    lands where the field WAS: the click misses, the keys go nowhere, and Enter submits a
    blank. Two consecutive equal readings mean the animation has finished. Bounded, and
    the last reading wins: a field that never holds still is still better clicked than not.
    """
    previous: dict[str, float] | None = None
    for _ in range(_SETTLE_READS):
        point = await _locate(window, _INPUT_RECT_EXPR)
        if point is None:
            return None
        if point == previous:
            return point
        previous = point
        await asyncio.sleep(_SETTLE_READ_GAP_SECONDS)
    return previous


async def _field_holds(window: WebWindow, password: str) -> bool:
    """True only when the visible field PROVABLY holds the typed number of characters.

    Only the length crosses back, never the value. An unreadable answer (no visible password
    field: WebK re-rendered) is "not held": Enter would go nowhere, and counting that as a
    submission would stop the window from ever typing again.
    """
    value = await _evaluate(window, _FIELD_LENGTH_EXPR)
    if not isinstance(value, int) or isinstance(value, bool):
        return False
    return value == len(password.encode("utf-16-le")) // 2


async def password_outcome(window: WebWindow) -> str:
    """After a submit: ``error`` (WebK shows the wrong-password state), ``checking`` or ``idle``."""
    value = await _evaluate(window, _SUBMIT_OUTCOME_EXPR)
    if value == "error":
        return "error"
    return "checking" if value == "checking" else "idle"


async def _type_into_field(window: WebWindow, point: dict[str, float], password: str) -> None:
    await _click_point(window, point)
    await asyncio.sleep(_CLICK_SETTLE_SECONDS)
    for kind in ("keyDown", "keyUp"):
        await _dispatch_key(
            window, {"type": kind, "key": "a", "code": "KeyA", "modifiers": _CTRL_MODIFIER}
        )
    for kind in ("keyDown", "keyUp"):
        await _dispatch_key(window, {"type": kind, "key": "Delete", "code": "Delete"})
    for char in password:
        await _dispatch_key(window, {"type": "keyDown", "text": char, "key": char})
        await _dispatch_key(window, {"type": "keyUp", "key": char})
        await asyncio.sleep(_KEY_GAP_SECONDS)


async def type_2fa_password(
    window: WebWindow, password: str, *, on_submit: Callable[[], None] | None = None
) -> bool:
    """Type the account's 2FA password into WebK's field and submit. Never logged.

    Waits for the field to hold still, real-mouse-clicks it to focus it (a JS focus leaves
    WebK's controlled input empty), clears it (Ctrl+A, Delete) and types each character
    with real CDP key events. Enter is pressed only once the field holds the right number
    of characters — a dropped keystroke would otherwise send Telegram a wrong password, and
    a wrong submit is the one thing that costs an attempt. False when nothing was submitted
    (no field yet, or it never took the value), so the caller may simply try again.

    ``on_submit`` runs synchronously right BEFORE Enter is dispatched: once it has run the
    password may have reached Telegram however this call ends (a dropped socket, a
    cancellation), so the caller counts the window as submitted and never types again.
    """
    point = await _settled_input_point(window)
    if point is None:
        return False
    for _ in range(_TYPE_ATTEMPTS):
        await _type_into_field(window, point, password)
        if await _field_holds(window, password):
            break
    else:
        return False
    if on_submit is not None:
        on_submit()
    await _dispatch_key(window, {"type": "keyDown", **_ENTER_KEY})
    # The keyDown is the submit; a socket that drops before the keyUp changes nothing.
    with suppress(CdpError, OSError):
        await _dispatch_key(window, {"type": "keyUp", **_ENTER_KEY})
    return True
