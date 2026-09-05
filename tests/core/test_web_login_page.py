"""The primitives that drive an already-open WebK page, against a recording session.

Split out of ``test_web_login_browser`` with the module they cover: the captured token
is read, the page is classified from STRUCTURE (never words — an account's locale
follows its proxy country), and the 2FA password is typed with real key events and
submitted with Enter.
"""

from __future__ import annotations

import json

import pytest

from core.web_login import _page
from core.web_login._page import (
    latest_login_token,
    page_state,
    release_capture,
    type_2fa_password,
)
from tests.core.web_login_helpers import PAGE, RecordingSession, window_for

# ------------------------------------------------------------------- latest_login_token


@pytest.mark.asyncio
async def test_latest_login_token_returns_the_freshest_captured() -> None:
    session = RecordingSession({_page._READ_CAPTURED_EXPR: json.dumps(["tok-a", "tok-b"])})
    assert await latest_login_token(window_for(session)) == "tok-b"


@pytest.mark.asyncio
async def test_latest_login_token_is_none_when_nothing_captured() -> None:
    session = RecordingSession({_page._READ_CAPTURED_EXPR: json.dumps([])})
    assert await latest_login_token(window_for(session)) is None


# --------------------------------------------------------------- release_capture


@pytest.mark.asyncio
async def test_release_capture_fires_the_hooks_own_teardown_on_the_page() -> None:
    session = RecordingSession()

    await release_capture(window_for(session))

    evals = [p["expression"] for m, p, _s in session.commands if m == "Runtime.evaluate"]
    assert evals == [_page._RELEASE_CAPTURE_EXPR]
    assert "__capOff" in _page._RELEASE_CAPTURE_EXPR
    assert all(target == PAGE for _m, _p, target in session.commands)


@pytest.mark.asyncio
async def test_release_capture_never_fails_a_window_that_has_just_logged_in() -> None:
    """Dropping the token array is hygiene, not correctness. A repeat open has no hook."""
    session = RecordingSession(fail_on="Runtime.evaluate")

    await release_capture(window_for(session))  # must not raise


# -------------------------------------------------------------------------- page_state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("info", "expected"),
    [
        # The 2FA screen: a VISIBLE password field, whatever it is labelled.
        ({"auth": True, "pw": True, "qr": False, "app": False}, "password"),
        # A German QR screen ("QR-Code", "Mit Telegram scannen") reads exactly like an
        # English one here: same canvas, same auth container, no words involved.
        ({"auth": True, "pw": False, "qr": True, "app": False}, "qr"),
        # Auth screen up but the canvas not painted yet — still not logged in.
        ({"auth": True, "pw": False, "qr": False, "app": False}, "qr"),
        # A shell marker showing while the auth screen is still up is NOT a login.
        ({"auth": True, "pw": False, "qr": True, "app": True}, "qr"),
        ({"auth": False, "pw": False, "qr": False, "app": True}, "logged_in"),
        ({"auth": False, "pw": False, "qr": False, "app": False}, "loading"),
    ],
)
async def test_page_state_classifies_the_visible_page(
    info: dict[str, object],
    expected: str,
) -> None:
    session = RecordingSession({_page._PAGE_STATE_EXPR: json.dumps(info)})
    assert await page_state(window_for(session)) == expected


def test_the_page_state_probe_reads_structure_not_language() -> None:
    """The account's locale follows its proxy country, and most of them are not English.

    A body-text probe matches nothing on a German QR screen, so the page is reported as
    logged in, the profile is marked signed-in, and every later click takes the
    already-signed-in path: the account is stranded on a QR screen for good.
    """
    probe = _page._PAGE_STATE_EXPR

    assert "innerText" not in probe
    assert "textContent" not in probe
    for phrase in ("qr code", "scan with telegram", "enter your password", "password to"):
        assert phrase not in probe.lower()
    assert not any(char.isalpha() and ord(char) > 127 for char in probe)
    # What it does read: the auth container, the QR canvas, a password input, the shell.
    assert "input[type=password]" in probe
    assert "canvas" in probe


@pytest.mark.asyncio
async def test_page_state_is_loading_when_the_probe_yields_no_value() -> None:
    session = RecordingSession()  # no scripted value -> {"result": {}}
    assert await page_state(window_for(session)) == "loading"


@pytest.mark.asyncio
async def test_page_primitives_address_the_page_session() -> None:
    session = RecordingSession({_page._PAGE_STATE_EXPR: json.dumps({"auth": True})})
    await page_state(window_for(session))
    assert all(target == PAGE for _m, _p, target in session.commands)


# --------------------------------------------------------------------- type_2fa_password


@pytest.mark.asyncio
async def test_type_2fa_password_clicks_field_types_and_submits_with_enter() -> None:
    session = RecordingSession({_page._INPUT_RECT_EXPR: '{"x":100,"y":200}'})

    await type_2fa_password(window_for(session), "pw1")

    key_events = [
        params for method, params, _s in session.commands if method == "Input.dispatchKeyEvent"
    ]
    downs = [e for e in key_events if e["type"] == "keyDown"]
    typed = "".join(str(e.get("text", "")) for e in downs if e.get("key") != "Enter")
    assert typed == "pw1"  # the password chars went in as real key text
    assert {"type": "keyDown", "key": "a", "code": "KeyA", "modifiers": 2} in key_events

    # A real mouse click focuses the visible field; only that one click is needed.
    clicks = [
        (p["x"], p["y"])
        for method, p, _s in session.commands
        if method == "Input.dispatchMouseEvent" and p["type"] == "mousePressed"
    ]
    assert clicks == [(100, 200)]

    evals = [
        params["expression"]
        for method, params, _s in session.commands
        if method == "Runtime.evaluate"
    ]
    assert evals == [_page._INPUT_RECT_EXPR]


@pytest.mark.asyncio
async def test_the_password_is_submitted_with_enter_not_a_translated_button() -> None:
    """A German 2FA screen says "Weiter", so a /next|log in/ button hunt finds nothing.

    The password would then be typed and never submitted, and the login stalls in
    silence. Enter is the same key in every language.
    """
    session = RecordingSession({_page._INPUT_RECT_EXPR: '{"x":100,"y":200}'})

    await type_2fa_password(window_for(session), "pw1")

    key_events = [
        params for method, params, _s in session.commands if method == "Input.dispatchKeyEvent"
    ]
    # Enter goes LAST, after every character of the password.
    assert [e["type"] for e in key_events[-2:]] == ["keyDown", "keyUp"]
    assert all(e["key"] == "Enter" and e["code"] == "Enter" for e in key_events[-2:])
    assert key_events[-2]["windowsVirtualKeyCode"] == 13
    # The ONLY mouse events are the two that focus the field — nothing clicks a submit
    # button afterwards, which is the mechanism a label hunt would have needed.
    mouse = [i for i, (m, _p, _s) in enumerate(session.commands) if m == "Input.dispatchMouseEvent"]
    first_key = next(i for i, (m, _p, _s) in enumerate(session.commands) if m.endswith("KeyEvent"))
    assert len(mouse) == 2
    assert max(mouse) < first_key


def test_the_password_field_probe_targets_a_visible_password_input() -> None:
    """Matching any wide input would type the cloud password into whatever comes first."""
    probe = _page._INPUT_RECT_EXPR

    assert "input[type=password]" in probe
    assert "querySelectorAll('input')" not in probe
    assert "height>0" in probe  # the hidden decoy input has zero height
