"""The ``open_account_web`` orchestrator: proxy gate, drive-once, relay/window reuse.

Every live collaborator is faked at this module's own globals (the re-export
contract) by ``_web_login_support``. The tests here pin the branch logic — no proxy is
refused, a fresh profile drives the QR login exactly once, a stored 2FA password is
typed when WebK asks, an already signed-in profile launches WITHOUT driving, a
still-open window is raised instead of duplicated, and both the relay and the window
survive to the next click (a closed socket would strip the account's fingerprint off
the browser). What happens when that raised window never signed in, when the account's
proxy changes under it, and how the refusals are worded live in
``test_web_login_reuse``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from schemas.proxy import ProxyCheckResult
from services.accounts import _web_drive, web_login
from services.accounts.web_login import (
    NoProxyForWebLoginError,
    WebLoginLaunchError,
    open_account_web,
)
from tests.services.accounts._web_login_support import PROXY as _PROXY
from tests.services.accounts._web_login_support import (
    SHIPPED_POLL_INTERVAL as _SHIPPED_POLL_INTERVAL,
)
from tests.services.accounts._web_login_support import SIGNED_IN_MARKER as _MARKER
from tests.services.accounts._web_login_support import FakeRelay as _FakeRelay
from tests.services.accounts._web_login_support import browser_filled as _browser_filled
from tests.services.accounts._web_login_support import counted_states as _counted_states
from tests.services.accounts._web_login_support import fresh_registry, relay
from tests.services.accounts._web_login_support import patch_profile as _patch_profile
from tests.services.accounts._web_login_support import patch_proxy as _patch_proxy
from tests.services.accounts._web_login_support import seeded as _seeded
from tests.services.accounts._web_login_support import wire as _wire

if TYPE_CHECKING:
    from pathlib import Path

    from schemas.proxy import ProxySettings

# Re-bound so pytest collects the shared fixtures in this module's namespace.
__all__ = ["fresh_registry", "relay"]


@pytest.mark.asyncio
async def test_no_proxy_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_proxy(monkeypatch, None)
    _patch_profile(monkeypatch, tmp_path / "acct")

    with pytest.raises(NoProxyForWebLoginError):
        await open_account_web("acct")


@pytest.mark.asyncio
async def test_an_unchecked_proxy_has_its_exit_country_measured_before_the_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],  # noqa: ARG001 - the launch needs a relay port
) -> None:
    """A freshly added proxy has no stored country, and that is the COMMON case.

    Falling through to the no-country default there would give the window a timezone
    unrelated to the exit IP, and timezone-versus-geolocation is the most routinely
    computed geo check there is. The probe is the same one the proxy pool runs.
    """
    calls: dict[str, Any] = {}
    probed = _patch_proxy(monkeypatch, _PROXY, stored_country=None, probed_country="NL")
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["qr", "logged_in"])

    await open_account_web("acct")

    assert probed == [_PROXY]
    assert calls["fingerprint"].timezone == "Europe/Amsterdam"
    assert calls["fingerprint"].locale == "nl-NL"


@pytest.mark.asyncio
async def test_a_country_a_check_already_resolved_wins_over_the_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],  # noqa: ARG001 - the launch needs a relay port
) -> None:
    """The probe still runs — it is the liveness gate — but the stored country stands."""
    calls: dict[str, Any] = {}
    probed = _patch_proxy(monkeypatch, _PROXY, stored_country="DE", probed_country="NL")
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["qr", "logged_in"])

    await open_account_web("acct")

    assert probed == [_PROXY]
    assert calls["fingerprint"].timezone == "Europe/Berlin"


@pytest.mark.asyncio
async def test_a_dead_proxy_is_refused_before_any_relay_or_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    """A dead proxy used to cost the whole 90 s drive and end in "not signed in".

    The relay starts fine without ever touching the upstream, Chrome launches, and WebK
    just never loads — so nothing failed until the drive deadline. The probe says so up
    front, with a code the SPA translates.
    """
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY, alive=False)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["qr"])

    with pytest.raises(WebLoginLaunchError) as caught:
        await open_account_web("acct")

    assert str(caught.value) == "web_login_proxy_dead"
    assert relay.created == []
    assert calls.get("launches") is None


@pytest.mark.usefixtures("relay")
@pytest.mark.parametrize(("stored", "geolocate"), [("DE", False), (None, True)])
@pytest.mark.asyncio
async def test_the_probe_bounds_liveness_only_and_geolocates_only_when_needed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stored: str | None,
    geolocate: bool,  # noqa: FBT001 - a parametrized expectation, not a flag argument
) -> None:
    """The bound covers the exit-IP fetch, never geolocation.

    Wrapping the whole check refused a LIVE proxy whenever a geo provider was slow —
    even for an account whose stored country needs no geolocation at all.
    """
    calls: dict[str, Any] = {}
    seen: list[dict[str, object]] = []
    _patch_proxy(monkeypatch, _PROXY, stored_country=stored)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["logged_in"])

    async def _check(_proxy: ProxySettings, **bounds: object) -> ProxyCheckResult:
        seen.append(bounds)
        return ProxyCheckResult(status="tcp_working", country_code="NL")

    monkeypatch.setattr(web_login, "check_proxy_connectivity", _check)

    await open_account_web("acct")

    assert seen == [{"exit_ip_timeout": web_login._PROXY_PROBE_TIMEOUT, "geolocate": geolocate}]


def test_the_shipped_probe_bound_is_a_few_seconds() -> None:
    assert web_login._PROXY_PROBE_TIMEOUT <= 5.0


@pytest.mark.asyncio
async def test_first_open_accepts_the_token_once_and_holds_the_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    calls: dict[str, Any] = {}
    profile = tmp_path / "acct"  # does not exist yet -> first open drives login
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, profile)
    window = _wire(monkeypatch, calls, states=["qr", "logged_in"])

    result = await open_account_web("acct")

    assert result.launched is True
    assert len(calls["accepted"]) == 1  # one QR token accepted, then logged in
    assert "typed" not in calls  # no password screen -> no 2FA typing
    relay_port, launched_dir, capture = calls["launched"]
    assert launched_dir == profile
    assert capture is True  # a fresh profile needs the QR hook
    assert relay_port == relay.created[0].port
    assert result.signed_in is True
    assert result.reason is None  # nothing to explain
    # A completed login is what marks the profile — nothing else does.
    assert (profile / _MARKER).exists()
    # The window is KEPT: closing its socket would strip the fingerprint.
    assert window.closed is False
    assert web_login._windows["acct"] is window


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_captured_token_is_accepted_before_the_qr_is_painted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """WebK asks for auth.exportLoginToken BEFORE it paints the canvas.

    So the capture hook holds the token while ``page_state`` still reads "loading".
    Waiting for the paint before accepting throws a whole poll interval away on every
    single login, for nothing: repeat accepts are already made safe by the ``accepted``
    set and by a rotated token simply being refused.
    """
    calls: dict[str, Any] = {}
    profile = tmp_path / "acct"
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, profile)
    _wire(monkeypatch, calls, states=["loading", "logged_in"])

    result = await open_account_web("acct")

    assert calls.get("accepted") == [b"token1"]  # accepted on the pre-QR poll
    assert result.signed_in is True
    assert (profile / _MARKER).exists()


def test_the_drive_poll_is_not_most_of_the_login() -> None:
    """Two transitions are only ever noticed by this poll, on a 4.5-17 s login.

    At 1.75 s that was 10-40% of the operator's wall clock spent in ``asyncio.sleep``,
    against a per-poll cost of one or two CDP round trips on loopback.
    """
    assert _SHIPPED_POLL_INTERVAL <= 0.5


@pytest.mark.asyncio
async def test_the_fingerprint_follows_the_proxy_country(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)  # country "DE"
    _patch_profile(monkeypatch, tmp_path / "acct")
    monkeypatch.setattr(web_login, "LocalProxyRelay", _FakeRelay)
    _FakeRelay.created = []
    _wire(monkeypatch, calls, states=["logged_in"])

    await open_account_web("acct")

    fingerprint = calls["fingerprint"]
    assert fingerprint.timezone == "Europe/Berlin"
    assert fingerprint.locale == "de-DE"


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_first_open_types_stored_password_on_the_password_screen(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["qr", "password"], twofa="hunter2")

    result = await open_account_web("acct")

    assert result.launched is True
    # A LIST, so "once" is provable: a second ``auth.checkPassword`` against a live
    # account with a stale stored password walks it towards a FLOOD_WAIT.
    assert calls["typed"] == ["hunter2"]
    assert len(calls["accepted"]) == 1
    assert result.signed_in is False  # the screen was left standing, not completed
    # We typed it and WebK stayed put: the stored password is what is wrong.
    assert result.reason == "web_login_2fa_rejected"


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_password_that_never_reached_the_field_is_not_called_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The typing helper found no field to click, so nothing was submitted.

    Blaming the stored password would send the operator to fix a credential Telegram
    never saw; the screen is simply waiting for a hand-typed one.
    """
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["password"], twofa="hunter2")

    async def _no_field(_window: object, password: str) -> bool:
        calls.setdefault("typed", []).append(password)
        return False

    monkeypatch.setattr(_web_drive, "type_2fa_password", _no_field)

    result = await open_account_web("acct")

    assert calls["typed"] == ["hunter2"]  # it was attempted, once...
    assert result.reason == "web_login_2fa_required"  # ...but never submitted


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_password_that_logs_in_reports_signed_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The 2FA path that actually SUCCEEDS — every other 2FA test leaves via the grace.

    ``fresh_registry`` zeroes ``_PASSWORD_GRACE``, so a scripted screen that never
    changes always exits through the expiry branch: a regression that returned False
    right after typing would pass the whole suite.
    """
    calls: dict[str, Any] = {}
    profile = tmp_path / "acct"
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, profile)
    _wire(monkeypatch, calls, states=["qr", "password", "logged_in"], twofa="hunter2")

    result = await open_account_web("acct")

    assert calls["typed"] == ["hunter2"]
    assert result.signed_in is True
    assert (profile / _MARKER).exists()


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_password_screen_without_stored_password_still_launches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["password"], token=None, twofa=None)

    result = await open_account_web("acct")

    assert result.launched is True  # operator sees the blank password screen
    assert "typed" not in calls  # nothing stored -> nothing typed
    # ...and "launched" is NOT "signed in": the toast has to say so.
    assert result.signed_in is False
    assert result.reason == "web_login_2fa_required"  # the operator types it by hand


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_signed_in_profile_launches_without_driving(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, _seeded(tmp_path / "acct"))
    _wire(monkeypatch, calls, states=["logged_in"])
    seen = _counted_states(monkeypatch, ["logged_in"])

    result = await open_account_web("acct")

    assert result.launched is True
    _port, _dir, capture = calls["launched"]
    # No QR hook: accepting a second token would spawn another Active Sessions device.
    assert capture is False
    assert "accepted" not in calls
    assert "typed" not in calls
    # ONE reading — the check that the stored session is still alive — never a drive
    # loop, which would poll again after accepting.
    assert seen == ["logged_in"]
    assert result.signed_in is True


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_login_that_never_finished_does_not_mark_the_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The drive timed out on the QR screen — Chrome still filled the directory."""
    calls: dict[str, Any] = {}
    profile = tmp_path / "acct"
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, profile)
    monkeypatch.setattr(web_login, "_DRIVE_TIMEOUT", 0.05)
    _wire(monkeypatch, calls, states=["qr"], token=None)

    result = await open_account_web("acct")

    assert result.signed_in is False  # the body, not just the marker, has to say so
    assert result.reason == "web_login_qr_timeout"
    assert any(profile.iterdir())  # Chrome wrote to it...
    assert not (profile / _MARKER).exists()  # ...but nobody signed in


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_page_that_never_loads_is_told_apart_from_an_unanswered_qr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Both end on the drive deadline, but they ask the operator for different things.

    A QR nobody accepted in time is worth another click; a WebK still reading "loading"
    after 90 s almost always means the account's proxy cannot reach Telegram, and
    clicking again changes nothing.
    """
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    monkeypatch.setattr(web_login, "_DRIVE_TIMEOUT", 0.05)
    _wire(monkeypatch, calls, states=["loading"], token=None)

    result = await open_account_web("acct")

    assert result.signed_in is False
    assert result.reason == "web_login_page_not_loaded"


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_browser_filled_profile_still_drives_the_login(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The trap the marker exists for: files alone must not count as signed in.

    Keyed on "the directory is non-empty", a first open that failed would send every
    later click down the already-signed-in path, leaving the operator stuck on a QR
    screen forever with no way out but deleting the profile by hand.
    """
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, _browser_filled(tmp_path / "acct"))
    _wire(monkeypatch, calls, states=["qr", "logged_in"])

    await open_account_web("acct")

    _port, _dir, capture = calls["launched"]
    assert capture is True  # the QR hook went back in
    assert len(calls["accepted"]) == 1  # and the login was driven to completion


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_still_open_window_is_raised_not_duplicated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    window = _wire(monkeypatch, calls, states=["logged_in"])

    await open_account_web("acct")
    await open_account_web("acct")

    assert calls["launches"] == 1  # the second click launched nothing
    assert calls["focused"] == 1  # it raised the window that was already open
    assert window.closed is False


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_raising_an_open_window_does_not_probe_the_proxy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, Any] = {}
    probed = _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["logged_in"])

    await open_account_web("acct")
    await open_account_web("acct")

    assert probed == [_PROXY]  # the first open only; the raise skipped it


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_closed_window_is_relaunched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    window = _wire(monkeypatch, calls, states=["logged_in"])

    await open_account_web("acct")
    window.alive = False  # the operator closed the browser
    await open_account_web("acct")

    assert calls["launches"] == 2
    assert "focused" not in calls
    # KILLED, not merely detached. ``aclose`` strips the fingerprint and leaves Chrome
    # running: it keeps holding the profile (so the next launch fails as a hand-off)
    # and keeps --proxy-server on a relay port this very open can rebind to ANOTHER
    # account's proxy. ``closed`` alone cannot tell the two apart — kill() closes too.
    assert window.killed is True


@pytest.mark.asyncio
async def test_second_click_reuses_the_same_relay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["logged_in"])

    await open_account_web("acct")
    await open_account_web("acct")

    assert calls["focused"] == 1  # the window was still open, so it was raised
    assert len(relay.created) == 1  # one relay for both clicks
    assert relay.created[0].starts == 1


@pytest.mark.asyncio
async def test_relay_start_failure_maps_to_launch_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _DeadRelay:
        def __init__(self, upstream: ProxySettings) -> None: ...

        async def start(self) -> int:
            msg = "bind refused"
            raise OSError(msg)

    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    monkeypatch.setattr(web_login, "LocalProxyRelay", _DeadRelay)

    with pytest.raises(WebLoginLaunchError):
        await open_account_web("acct")


@pytest.mark.asyncio
async def test_shutdown_kills_and_clears_windows_and_relays(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    window = _wire(monkeypatch, calls, states=["logged_in"])

    await open_account_web("acct")
    assert relay.created[0].closed is False
    assert window.closed is False

    await web_login.shutdown_web_login()

    assert window.closed is True
    # KILLED, not just detached: this shutdown frees the relay's loopback port, and a
    # leftover window still carrying --proxy-server for it would, after a restart,
    # egress through whichever account's relay binds that port next.
    assert window.killed is True
    assert relay.created[0].closed is True
    assert web_login._windows == {}
    assert web_login._relays == {}


@pytest.mark.asyncio
async def test_an_open_after_shutdown_started_registers_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    """``shutdown_web_login`` takes the two registry locks — not the per-account one.

    So a click racing it would register a fresh relay and a fresh window AFTER both
    lists were read and cleared, leaking a live Chrome and a listening socket past
    process exit. Nothing running can clean those up; the click has to be refused.
    """
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire(monkeypatch, calls, states=["logged_in"])

    await web_login.shutdown_web_login()
    with pytest.raises(WebLoginLaunchError):
        await open_account_web("acct")

    assert "launched" not in calls
    assert relay.created == []
    assert web_login._windows == {}
    assert web_login._relays == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("lands_in", ["relay_start", "launch"])
async def test_a_shutdown_landing_mid_open_registers_neither_half(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
    lands_in: str,
) -> None:
    """Shutdown-then-click proves the flag is read; it never exercises the interleaving.

    The flag is read once, before ``relay.start()``, the exit-country probe, the spawn
    and a DevTools wait bounded at 20 s — and the two registrations happen after all of
    them. A shutdown landing inside that window clears both registries and is then
    handed a fresh relay and a fresh window to put back into them: a Chrome surviving
    process exit with its fingerprint stripped, holding this account's profile (so every
    later open fails as a hand-off) and pointed at a loopback port the shutdown just
    freed, which another account's relay can bind after a restart.
    """
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    window = _wire(monkeypatch, calls, states=["logged_in"])
    launch = web_login.launch_account_web

    class _ShuttingRelay(_FakeRelay):
        """Shutdown lands while this account's relay is still starting."""

        async def start(self) -> int:
            port = await super().start()
            if lands_in == "relay_start":
                await web_login.shutdown_web_login()
            return port

    async def _launch_then_shutdown(relay_port: int, **kwargs: Any) -> Any:
        opened = await launch(relay_port, **kwargs)
        await web_login.shutdown_web_login()
        return opened

    monkeypatch.setattr(web_login, "LocalProxyRelay", _ShuttingRelay)
    if lands_in == "launch":
        monkeypatch.setattr(web_login, "launch_account_web", _launch_then_shutdown)

    # The leak is what matters, so it is asserted before the refusal that prevents it.
    refused: Exception | None = None
    try:
        await open_account_web("acct")
    except WebLoginLaunchError as exc:
        refused = exc

    assert web_login._relays == {}  # the teardown's emptied dicts stay empty
    assert web_login._windows == {}
    assert relay.created[0].closed is True  # the in-flight relay is closed, not kept
    if lands_in == "launch":
        assert window.killed is True  # ...and the in-flight window is ended, not kept
    else:
        assert "launched" not in calls  # no browser is even spawned this late
    assert refused is not None  # ...and the click is refused, not reported as an open
