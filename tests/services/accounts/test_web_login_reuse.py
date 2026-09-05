"""Reopening an account's window: what the second click does, and what it may claim.

Everything here is about state that OUTLIVES one open — the still-registered window,
the cached relay, the profile marker — plus the wording the operator finally sees.
Same doubles as ``test_web_login``, from ``_web_login_support``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from core.web_login import browser as _browser
from core.web_login import fingerprint as _fingerprint
from schemas.proxy import ProxySettings
from services.accounts import _web_drive, web_login
from services.accounts.web_login import WebLoginLaunchError, open_account_web
from tests.services.accounts._web_login_support import PROXY as _PROXY
from tests.services.accounts._web_login_support import SIGNED_IN_MARKER as _MARKER
from tests.services.accounts._web_login_support import FakeRelay as _FakeRelay
from tests.services.accounts._web_login_support import counted_states as _counted_states
from tests.services.accounts._web_login_support import fresh_registry, relay
from tests.services.accounts._web_login_support import patch_page_state as _patch_page_state
from tests.services.accounts._web_login_support import patch_profile as _patch_profile
from tests.services.accounts._web_login_support import patch_proxy as _patch_proxy
from tests.services.accounts._web_login_support import scripted_states as _scripted_states
from tests.services.accounts._web_login_support import seeded as _seeded
from tests.services.accounts._web_login_support import wire as _wire

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Re-bound so pytest collects the shared fixtures in this module's namespace.
__all__ = ["fresh_registry", "relay"]


# ------------------------------------------------- an open window that never signed in


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_raised_window_whose_login_never_finished_is_driven_again(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Focusing a QR screen and calling it a success strands the account forever.

    The window stays registered and ``alive``, so without this every later click just
    raises it and returns "launched" without ever driving login again — and the marker
    only helps once the operator closes the window, which nothing tells them to do.
    Driving again is safe precisely because no login completed: it cannot spawn a
    second 'Active Sessions' device.
    """
    calls: dict[str, Any] = {}
    profile = tmp_path / "acct"
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, profile)
    monkeypatch.setattr(web_login, "_DRIVE_TIMEOUT", 0.05)
    _wire(monkeypatch, calls, states=["qr"], token=None)  # never reaches logged_in

    first = await open_account_web("acct")
    assert first.signed_in is False
    assert not (profile / _MARKER).exists()

    # Second click: the window is still open, so it is raised — and driven again.
    _patch_page_state(monkeypatch, _scripted_states(["qr", "logged_in"]))
    second = await open_account_web("acct")

    assert calls["launches"] == 1  # no duplicate window
    assert calls["focused"] == 1  # the open one was raised
    assert second.signed_in is True  # ...and the login was actually driven this time
    assert (profile / _MARKER).exists()


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_the_token_capture_is_released_only_once_the_login_has_completed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The hook holds live login tokens — and it is the thing that makes login possible.

    Tearing it down early is not a tidy-up, it is the failure: the round that let the
    page decide killed the capture while the QR screen was still up, and no token was
    ever read. So the teardown fires from here, at the one moment that is provably
    after the login, and a drive that never gets there must leave the hook alone.
    """
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    monkeypatch.setattr(web_login, "_DRIVE_TIMEOUT", 0.05)
    _wire(monkeypatch, calls, states=["qr"])  # never reaches logged_in

    first = await open_account_web("acct")

    assert first.signed_in is False
    assert calls.get("released") is None  # the capture is still armed for the next token
    assert calls["accepted"]  # ...and it did feed a token in meanwhile

    _patch_page_state(monkeypatch, _scripted_states(["qr", "logged_in"]))
    second = await open_account_web("acct")

    assert second.signed_in is True
    assert calls["released"] == 1


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_raised_window_on_the_password_screen_types_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Re-driving a raised window must NOT re-submit the stored 2FA password.

    A stale stored password is an ordinary ops state: click one types it, Telegram
    refuses it, and the window is left standing on the password screen. The operator
    sees the failure toast and clicks again — and every click would be another
    ``auth.checkPassword`` failure against a live account, walking it into a FLOOD_WAIT
    or a temporary lock with nothing on screen to say so. Only the QR half of the
    re-drive is safe to repeat.
    """
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["qr", "password"], twofa="hunter2")

    first = await open_account_web("acct")
    assert first.signed_in is False
    assert calls["typed"] == ["hunter2"]  # the ONE submission this window ever gets

    # Second click: the window is still open on the password screen and is driven
    # again — for the QR half, which is what re-driving exists for.
    calls["typed"] = []
    _patch_page_state(monkeypatch, _scripted_states(["password"]))
    second = await open_account_web("acct")

    assert second.signed_in is False
    assert calls["focused"] == 1  # it really was the raised-window path
    assert calls["typed"] == []  # ...and it typed NOTHING


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_raised_signed_in_window_is_not_driven_again(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The other half of the rule: a completed login must never be driven twice.

    Pinned on the SEEDED check alone — the profile is marked before the first click,
    and the window is launched already signed in — so the old "a raised window is an
    unconditional success" behaviour cannot make this pass by accident.
    """
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, _seeded(tmp_path / "acct"))
    _wire(monkeypatch, calls, states=["logged_in"])
    seen = _counted_states(monkeypatch, ["logged_in"])

    await open_account_web("acct")
    second = await open_account_web("acct")

    assert second.signed_in is True
    assert calls["focused"] == 1  # the second click raised the open window
    # One reading per click — the still-signed-in check — and no drive loop, which
    # would accept a token and spawn a second 'Active Sessions' device.
    assert seen == ["logged_in", "logged_in"]
    assert "accepted" not in calls


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_dead_web_session_under_the_marker_is_driven_again(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The marker is a one-way on-disk latch and nothing ever deleted it.

    Revoke the session from Active Sessions (or log out inside the window, or clear
    the profile data) and every later click returned ``signed_in=True`` with no probe,
    no toast and no re-drive — permanently, and across backend restarts. Exactly the
    "screen no amount of clicking gets past" trap the marker exists to prevent,
    reached from the other side.
    """
    calls: dict[str, Any] = {}
    profile = _seeded(tmp_path / "acct")
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, profile)
    window = _wire(monkeypatch, calls, states=["logged_in"])

    await open_account_web("acct")

    # The web session dies while the window stays open.
    _patch_page_state(monkeypatch, _scripted_states(["qr", "logged_in"]))
    second = await open_account_web("acct")

    assert window.killed is True  # the hookless window cannot be driven — it is ended
    assert calls["launches"] == 2  # ...and replaced by one that can
    _port, _dir, capture = calls["launched"]
    assert capture is True  # the QR hook goes back in, decided before the launch
    assert second.signed_in is True
    assert (profile / _MARKER).exists()


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_dead_web_session_is_caught_on_a_cold_launch_too(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """After a restart there is no window to raise, so the launch path must check too.

    Without this the marker survives the restart and the very first click reports a
    signed-in account that is sitting on a QR screen.
    """
    calls: dict[str, Any] = {}
    profile = _seeded(tmp_path / "acct")
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, profile)
    _wire(monkeypatch, calls, states=["qr", "qr", "logged_in"])

    result = await open_account_web("acct")

    assert calls["launches"] == 2  # the hookless first window was replaced
    _port, _dir, capture = calls["launched"]
    assert capture is True
    assert result.signed_in is True
    assert len(calls["accepted"]) == 1


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_drive_that_never_signed_in_reports_signed_in_false(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``launched`` alone cannot tell a clean login from 90 s of refused tokens."""
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    monkeypatch.setattr(web_login, "_DRIVE_TIMEOUT", 0.05)
    _wire(monkeypatch, calls, states=["qr"], token=None)

    result = await open_account_web("acct")

    assert result.launched is True
    assert result.signed_in is False


# ------------------------------------------------------------------ locking behaviour


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_focusing_a_window_does_not_hold_the_registry_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``focus_window`` is a CDP command bounded at 30 s, not a dict read.

    Awaiting it under the process-wide ``_windows_lock`` lets one hung Chrome block
    EVERY other account's open for 30 s — and block shutdown — which is exactly what
    the per-account open lock exists to prevent.
    """
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, _seeded(tmp_path / "acct"))
    _wire(monkeypatch, calls, states=["logged_in"])
    held: list[bool] = []

    async def _slow_focus(_window: object) -> None:
        held.append(web_login._windows_lock.locked())
        await asyncio.sleep(0)

    monkeypatch.setattr(web_login, "focus_window", _slow_focus)

    await open_account_web("acct")
    await open_account_web("acct")

    assert held == [False]


# ----------------------------------------------------------------------- relay reuse


@pytest.mark.asyncio
async def test_a_reassigned_proxy_rebinds_the_relay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    """A cached relay still fronts the OLD proxy after the account is reassigned.

    Reused, the window keeps exiting through the previous proxy for the life of the
    process — and once that proxy row is deleted, every page load 502s. The window is
    left OPEN here on purpose: the rebind has to happen before the reuse check, or it
    is unreachable in exactly the case that matters, and the operator has no way to
    recover by clicking.
    """
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    window = _wire(monkeypatch, calls, states=["logged_in"])

    await open_account_web("acct")
    moved = ProxySettings(
        proxy_type="socks5", host="other.example", port=1080, username="u", password="p"
    )
    _patch_proxy(monkeypatch, moved)
    await open_account_web("acct")

    assert len(relay.created) == 2  # the stale one was replaced...
    assert relay.created[0].closed is True  # ...and closed, not orphaned
    assert relay.created[1].upstream == moved
    assert web_login._relays["acct"][1] == moved
    # The window pointed at the old relay's port is ended, not raised.
    assert window.killed is True
    assert "focused" not in calls
    assert calls["launches"] == 2
    port, _dir, _capture = calls["launched"]
    assert port == relay.created[1].port


@pytest.mark.asyncio
async def test_closing_a_stale_relay_does_not_hold_the_registry_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    """``relay.aclose`` cancels and AWAITS every in-flight tunnel task.

    Holding the process-wide ``_relays_lock`` across that stalls every other account's
    open behind one busy relay — the same mistake ``_raise_open_window`` was just fixed
    for, argued at length in its own docstring.
    """
    held: list[bool] = []

    class _WatchingRelay(_FakeRelay):
        async def aclose(self) -> None:
            held.append(web_login._relays_lock.locked())
            await asyncio.sleep(0)
            await super().aclose()

    monkeypatch.setattr(web_login, "LocalProxyRelay", _WatchingRelay)
    relay.created = []
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["logged_in"])

    await open_account_web("acct")
    _patch_proxy(
        monkeypatch,
        ProxySettings(
            proxy_type="socks5", host="other.example", port=1080, username="u", password="p"
        ),
    )
    await open_account_web("acct")

    assert held == [False]


# --------------------------------------------------------------------- fixed refusals


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_third_party_error_in_the_drive_becomes_a_fixed_refusal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``token_bytes`` raises binascii.Error, ``get_client`` raises Telethon errors.

    Both are outside the old ``(CdpError, OSError)`` net, so they became bare 500s;
    piping ``str(exc)`` instead would put third-party wording — and sometimes an
    absolute host path — straight into the operator's toast.
    """
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["qr"])
    leak = r"AuthKeyUnregisteredError at C:\Users\operator\sessions\acct.session"

    async def _boom(_account_id: str, _token: bytes) -> None:
        raise RuntimeError(leak)

    monkeypatch.setattr(_web_drive, "accept_web_login_token", _boom)

    with pytest.raises(WebLoginLaunchError) as caught:
        await open_account_web("acct")

    assert leak not in str(caught.value)
    assert "AuthKeyUnregistered" not in str(caught.value)


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_the_reload_event_loop_is_refused_by_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A Windows SelectorEventLoop implements no subprocess transport at all.

    ``uvicorn --reload`` (and ``--workers N``) forces one, so
    ``create_subprocess_exec`` raises NotImplementedError — outside every caught tuple
    and outside the service-error mapper, i.e. a bare 500 with a traceback under the
    documented dev command.
    """
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["logged_in"])

    async def _no_subprocess(*_args: object, **_kwargs: object) -> None:
        raise NotImplementedError

    monkeypatch.setattr(web_login, "launch_account_web", _no_subprocess)
    monkeypatch.setattr(web_login, "_loop_cannot_spawn", lambda: True)

    with pytest.raises(WebLoginLaunchError) as caught:
        await open_account_web("acct")

    assert str(caught.value) == web_login._RELOAD_LOOP_REFUSAL


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_notimplementederror_on_a_capable_loop_is_the_generic_refusal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``except NotImplementedError`` wraps the WHOLE launch, not just the spawn.

    Anything under it that is simply unimplemented — a driver path, a stub in a
    collaborator — would otherwise tell the operator to restart uvicorn without
    ``--reload`` on a server that never had it, sending them after a setting that is
    already right. The running loop is what decides that wording.
    """
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["logged_in"])

    async def _unimplemented(*_args: object, **_kwargs: object) -> None:
        raise NotImplementedError

    monkeypatch.setattr(web_login, "launch_account_web", _unimplemented)
    # The loop pytest-asyncio gives us really can spawn subprocesses, so nothing is
    # patched here: this is the predicate's own verdict.
    with pytest.raises(WebLoginLaunchError) as caught:
        await open_account_web("acct")

    assert str(caught.value) == web_login._LAUNCH_REFUSAL


def _shell_code_tables() -> list[tuple[str, dict[str, str]]]:
    """``shell.code`` out of each shipped locale file, by language."""
    i18n = _REPO_ROOT / "frontend" / "src" / "shared" / "i18n"
    return [
        (lang, json.loads((i18n / f"{lang}.json").read_text(encoding="utf-8"))["shell"]["code"])
        for lang in ("en", "ru")
    ]


def test_every_refusal_is_a_translatable_code() -> None:
    """The SPA translates the envelope message through ``shell.code.<code>``.

    An unknown string falls through verbatim, so English prose here reaches a
    Russian-first operator as English — while the neighbouring success-but-not-signed-in
    toast is localised.

    Enumerated, so it cannot see a refusal built from anything OTHER than these
    constants; the test below drives the path that used to be exactly that.
    """
    codes = [
        web_login._NO_PROXY_REFUSAL,
        web_login._LAUNCH_REFUSAL,
        web_login._DRIVE_REFUSAL,
        web_login._RELAY_REFUSAL,
        web_login._SHUTDOWN_REFUSAL,
        web_login._RELOAD_LOOP_REFUSAL,
    ]
    for code in codes:
        assert code == code.lower()
        assert " " not in code
        assert code.replace("_", "").isalnum()
    for lang, table in _shell_code_tables():
        for code in codes:
            assert code in table, f"{code} missing from {lang}.json"


@pytest.mark.usefixtures("relay")
@pytest.mark.parametrize("missing", ["executable", "version"])
@pytest.mark.asyncio
async def test_a_browser_that_cannot_be_read_refuses_with_a_translatable_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    missing: str,
) -> None:
    """The refusal is BUILT here, not listed — the only way this defect is visible.

    ``BrowserNotFoundError`` carries two English sentences, raised from inside the same
    ``try``: one when no chrome.exe/msedge.exe exists, one when the installed browser's
    version cannot be read (``fingerprint_for`` resolves the identity off the
    installation before the launch). Re-raised as ``str(exc)`` they went through the
    envelope's ``message`` into ``t('shell.code.<message>', {defaultValue: message})``
    and reached a Russian-first operator verbatim, untranslated in either locale.
    """
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["logged_in"])
    # Nothing patches ``fingerprint_for``: the real discovery runs, and is what raises.
    monkeypatch.setattr(_fingerprint, "_observed", {})
    if missing == "executable":
        # No candidate exists, so the REAL ``find_browser`` raises its own sentence.
        monkeypatch.setattr(_browser, "_candidate_browsers", list)
    else:
        # An installation with no version-named directory beside the executable, which
        # is where the claimed build is read from.
        empty_install = tmp_path / "browsers"
        empty_install.mkdir()
        monkeypatch.setattr(_browser, "find_browser", lambda: empty_install / "chrome.exe")

    with pytest.raises(WebLoginLaunchError) as caught:
        await open_account_web("acct")

    message = str(caught.value)
    assert calls.get("launches") is None  # it never got as far as a window
    for lang, table in _shell_code_tables():
        assert message in table, f"{message!r} is not a {lang}.json code — it is prose"


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_wedged_drive_is_bounded_by_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The deadline has to wrap the whole drive, not only the loop entry.

    Each CDP command has its own 30 s timeout and typing an N-character password
    issues 2N+8 of them, so a wedged-but-connected renderer inside the body would hold
    the HTTP request for many minutes. A timeout is "did not sign in", not an error.
    """
    calls: dict[str, Any] = {}
    profile = tmp_path / "acct"
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, profile)
    monkeypatch.setattr(web_login, "_DRIVE_TIMEOUT", 0.05)
    _wire(monkeypatch, calls, states=["qr"])

    async def _wedged(_window: object) -> str:
        await asyncio.sleep(3600)
        return "qr"

    _patch_page_state(monkeypatch, _wedged)

    result = await asyncio.wait_for(open_account_web("acct"), timeout=5)

    assert result.launched is True
    assert result.signed_in is False  # a timeout is not an error, and not a success
    assert not (profile / _MARKER).exists()
