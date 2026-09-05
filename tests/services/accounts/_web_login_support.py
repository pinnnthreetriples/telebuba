"""Shared doubles for the ``open_account_web`` tests — two modules stub the same seams.

``test_web_login.py`` (the launch/drive/reuse core) and ``test_web_login_reuse.py``
(raising an open window, the relay's proxy binding, the bounded refusals) patch the
same collaborators at the service module's own globals: the proxy/country/2FA lookups,
the relay, the launch, the page-state and token probes, the accept and the 2FA typing.
Kept here rather than as two drifting copies.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from schemas.proxy import ProxyCheckResult, ProxySettings
from services.accounts import _web_drive, web_login

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

PROXY = ProxySettings(
    proxy_type="socks5", host="proxy.example", port=1080, username="u", password="p"
)
# Captured at import, before ``fresh_registry`` zeroes it, so a test can still assert the
# cadence the backend actually ships rather than the test double's instant one.
SHIPPED_POLL_INTERVAL = _web_drive._POLL_INTERVAL
# The marker file lives with the code that writes it, so tests name it from there.
SIGNED_IN_MARKER = _web_drive._SIGNED_IN_MARKER


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the process-global registries per test and make the drive loop instant.

    The locks are loop-bound, so each test gets fresh ones (a Lock from a prior
    test's event loop would be rejected by this test's loop). The poll cadence and
    the post-2FA grace are zeroed so the drive loop does not really sleep.
    """
    monkeypatch.setattr(web_login, "_relays", {})
    monkeypatch.setattr(web_login, "_relays_lock", asyncio.Lock())
    monkeypatch.setattr(web_login, "_windows", {})
    monkeypatch.setattr(web_login, "_windows_lock", asyncio.Lock())
    monkeypatch.setattr(web_login, "_open_locks", {})
    monkeypatch.setattr(web_login, "_open_locks_guard", asyncio.Lock())
    monkeypatch.setattr(web_login, "_closing", asyncio.Event())
    monkeypatch.setattr(_web_drive, "_POLL_INTERVAL", 0)
    monkeypatch.setattr(_web_drive, "_PASSWORD_GRACE", 0)


class FakeRelay:
    """Records start/close and hands out a distinct port per instance."""

    created: list[FakeRelay] = []  # noqa: RUF012 - test double, reset per test below

    def __init__(self, upstream: ProxySettings) -> None:
        self.upstream = upstream
        self.starts = 0
        self.closed = False
        self._port: int | None = None
        FakeRelay.created.append(self)

    async def start(self) -> int:
        self.starts += 1
        self._port = 41000 + len(FakeRelay.created)
        return self._port

    @property
    def port(self) -> int | None:
        return self._port

    async def aclose(self) -> None:
        self.closed = True
        self._port = None


class FakeWindow:
    """The window a launch returns: alive until the operator (or shutdown) closes it."""

    def __init__(self) -> None:
        self.alive = True
        self.closed = False
        self.killed = False

    async def aclose(self) -> None:
        self.closed = True
        self.alive = False

    async def kill(self) -> None:
        await self.aclose()
        self.killed = True


@pytest.fixture
def relay(monkeypatch: pytest.MonkeyPatch) -> type[FakeRelay]:
    FakeRelay.created = []
    monkeypatch.setattr(web_login, "LocalProxyRelay", FakeRelay)
    return FakeRelay


def patch_proxy(
    monkeypatch: pytest.MonkeyPatch,
    proxy: ProxySettings | None,
    *,
    stored_country: str | None = "DE",
    probed_country: str | None = None,
) -> list[ProxySettings]:
    """Stub the proxy lookups; return the list the live connectivity probe records into.

    ``stored_country`` is what a past proxy check saved — ``None`` is the common state
    of a freshly added proxy, and the one that makes the service measure the exit
    country itself before the identity is built.
    """
    probed: list[ProxySettings] = []

    async def _fetch(account_id: str) -> ProxySettings | None:  # noqa: ARG001
        return proxy

    async def _country(account_id: str) -> str | None:  # noqa: ARG001
        return stored_country

    async def _check(settings: ProxySettings) -> ProxyCheckResult:
        probed.append(settings)
        return ProxyCheckResult(status="tcp_working", country_code=probed_country)

    monkeypatch.setattr(web_login, "fetch_account_proxy_settings", _fetch)
    monkeypatch.setattr(web_login, "fetch_account_proxy_country", _country)
    monkeypatch.setattr(web_login, "check_proxy_connectivity", _check)
    return probed


def patch_profile(monkeypatch: pytest.MonkeyPatch, profile: Path) -> None:
    monkeypatch.setattr(web_login, "account_profile_dir", lambda account_id: profile)  # noqa: ARG005


def seeded(profile: Path) -> Path:
    """A profile a login has actually completed in, marker and all."""
    profile.mkdir()
    (profile / "Default").write_text("chrome state", encoding="utf-8")
    (profile / SIGNED_IN_MARKER).touch()
    return profile


def browser_filled(profile: Path) -> Path:
    """A profile Chrome has written to but that never finished a login.

    This is what a first open that timed out (or that the operator closed) leaves
    behind — the trap the marker exists to avoid.
    """
    profile.mkdir()
    (profile / "Default").write_text("chrome state", encoding="utf-8")
    return profile


def patch_page_state(monkeypatch: pytest.MonkeyPatch, probe: Callable[[object], Any]) -> None:
    """Re-point the page probe after ``wire``; it lives on ``_web_drive``."""
    monkeypatch.setattr(_web_drive, "page_state", probe)


def counted_states(monkeypatch: pytest.MonkeyPatch, seq: Sequence[str]) -> list[str]:
    """Patch the page probe to walk ``seq``; the returned list records every reading.

    "Was the login driven?" is a question about how many times the page was READ:
    a drive loop polls again after accepting a token, while the already-signed-in
    check takes exactly one reading.
    """
    probe = scripted_states(seq)
    seen: list[str] = []

    async def _state(window: object) -> str:
        value = await probe(window)
        seen.append(value)
        return value

    patch_page_state(monkeypatch, _state)
    return seen


def scripted_states(seq: Sequence[str]) -> Callable[[object], Any]:
    """A ``page_state`` fake that walks ``seq`` then holds on its last value."""
    box = list(seq)

    async def _state(_window: object) -> str:
        return box.pop(0) if len(box) > 1 else box[0]

    return _state


def wire(
    monkeypatch: pytest.MonkeyPatch,
    calls: dict[str, Any],
    *,
    states: Sequence[str],
    token: str | None = "dG9rZW4x",  # base64url for b"token1"
    twofa: str | None = None,
) -> FakeWindow:
    """Wire the launch + drive collaborators; return the window the launch hands back."""
    window = FakeWindow()

    async def _launch(
        relay_port: int,
        *,
        profile_dir: Path,
        fingerprint: object,
        capture_tokens: bool,
    ) -> FakeWindow:
        calls["launched"] = (relay_port, profile_dir, capture_tokens)
        calls["fingerprint"] = fingerprint
        calls["launches"] = calls.get("launches", 0) + 1
        # The real launcher creates the profile dir before Chrome starts, and Chrome
        # fills it immediately — which is exactly why "has files" cannot mean signed in.
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "Default").write_text("chrome state", encoding="utf-8")
        return window

    async def _focus(_window: object) -> None:
        calls["focused"] = calls.get("focused", 0) + 1

    async def _latest(_window: object) -> str | None:
        return token

    async def _accept(account_id: str, token_bytes: bytes) -> None:  # noqa: ARG001
        calls.setdefault("accepted", []).append(token_bytes)

    async def _release(_window: object) -> None:
        calls["released"] = calls.get("released", 0) + 1

    async def _type(_window: object, password: str) -> None:
        # A LIST, never a scalar: assigning would overwrite, so "typed exactly once"
        # — the whole point of the 2FA re-submit guard — could not be asserted at all.
        calls.setdefault("typed", []).append(password)

    async def _twofa(_account_id: str) -> str | None:
        return twofa

    monkeypatch.setattr(web_login, "launch_account_web", _launch)
    monkeypatch.setattr(web_login, "focus_window", _focus)
    # The page-facing collaborators live in ``_web_drive``; patching them on
    # ``web_login`` would leave the real ones running and prove nothing.
    monkeypatch.setattr(_web_drive, "page_state", scripted_states(states))
    monkeypatch.setattr(_web_drive, "latest_login_token", _latest)
    monkeypatch.setattr(_web_drive, "accept_web_login_token", _accept)
    monkeypatch.setattr(_web_drive, "release_capture", _release)
    monkeypatch.setattr(_web_drive, "type_2fa_password", _type)
    monkeypatch.setattr(_web_drive, "fetch_account_twofa_password", _twofa)
    return window
