"""The ``open_account_web`` orchestrator: proxy gate, drive-once, relay reuse.

Every live collaborator is faked at this module's own globals (the re-export
contract): the proxy/2FA lookups, the :class:`LocalProxyRelay`, the hooked launch,
the page-state/token probes, the token accept, the 2FA typing and the relaunch. The
tests pin the branch logic — no proxy is refused, a fresh profile drives the QR
login exactly once, a stored 2FA password is typed when WebK asks, an already
signed-in profile relaunches WITHOUT driving, and a second click reuses the one relay.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from schemas.proxy import ProxySettings
from services.accounts import web_login
from services.accounts.web_login import (
    NoProxyForWebLoginError,
    WebLoginLaunchError,
    open_account_web,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

_PROXY = ProxySettings(
    proxy_type="socks5", host="proxy.example", port=1080, username="u", password="p"
)


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the process-global registries per test and make the drive loop instant.

    The locks are loop-bound, so each test gets fresh ones (a Lock from a prior
    test's event loop would be rejected by this test's loop). The poll cadence and
    the post-2FA grace are zeroed so the drive loop does not really sleep.
    """
    monkeypatch.setattr(web_login, "_relays", {})
    monkeypatch.setattr(web_login, "_relays_lock", asyncio.Lock())
    monkeypatch.setattr(web_login, "_open_locks", {})
    monkeypatch.setattr(web_login, "_open_locks_guard", asyncio.Lock())
    monkeypatch.setattr(web_login, "_POLL_INTERVAL", 0)
    monkeypatch.setattr(web_login, "_PASSWORD_GRACE", 0)


class _FakeRelay:
    """Records start/close and hands out a distinct port per instance."""

    created: list[_FakeRelay] = []  # noqa: RUF012 - test double, reset per test below

    def __init__(self, upstream: ProxySettings) -> None:
        self.upstream = upstream
        self.starts = 0
        self.closed = False
        self._port: int | None = None
        _FakeRelay.created.append(self)

    async def start(self) -> int:
        self.starts += 1
        self._port = 41000 + len(_FakeRelay.created)
        return self._port

    @property
    def port(self) -> int | None:
        return self._port

    async def aclose(self) -> None:
        self.closed = True
        self._port = None


class _FakeSession:
    """The CDP session the hooked launch returns; only aclose is exercised."""

    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def relay(monkeypatch: pytest.MonkeyPatch) -> type[_FakeRelay]:
    _FakeRelay.created = []
    monkeypatch.setattr(web_login, "LocalProxyRelay", _FakeRelay)
    return _FakeRelay


def _patch_proxy(monkeypatch: pytest.MonkeyPatch, proxy: ProxySettings | None) -> None:
    async def _fetch(account_id: str) -> ProxySettings | None:  # noqa: ARG001
        return proxy

    monkeypatch.setattr(web_login, "fetch_account_proxy_settings", _fetch)


def _patch_profile(monkeypatch: pytest.MonkeyPatch, profile: Path) -> None:
    monkeypatch.setattr(web_login, "account_profile_dir", lambda account_id: profile)  # noqa: ARG005


def _scripted_states(seq: Sequence[str]) -> Callable[[object], Any]:
    """A ``page_state`` fake that walks ``seq`` then holds on its last value."""
    box = list(seq)

    async def _state(_session: object) -> str:
        return box.pop(0) if len(box) > 1 else box[0]

    return _state


def _wire_drive(
    monkeypatch: pytest.MonkeyPatch,
    calls: dict[str, Any],
    *,
    states: Sequence[str],
    token: str | None = "dG9rZW4x",  # base64url for b"token1"
    twofa: str | None = None,
) -> _FakeSession:
    """Wire the hooked-launch + drive collaborators; return the fake session."""
    session = _FakeSession()

    async def _launch(relay_port: int, *, profile_dir: Path) -> tuple[_FakeSession, object]:
        calls["launched"] = (relay_port, profile_dir)
        return session, object()

    async def _latest(_session: object) -> str | None:
        return token

    async def _accept(account_id: str, token_bytes: bytes) -> None:  # noqa: ARG001
        calls.setdefault("accepted", []).append(token_bytes)

    async def _type(_session: object, password: str) -> None:
        calls["typed"] = password

    async def _twofa(_account_id: str) -> str | None:
        return twofa

    async def _relaunch(relay_port: int, *, profile_dir: Path) -> None:  # noqa: ARG001
        calls["relaunch"] = calls.get("relaunch", 0) + 1

    monkeypatch.setattr(web_login, "launch_webk_with_hook", _launch)
    monkeypatch.setattr(web_login, "page_state", _scripted_states(states))
    monkeypatch.setattr(web_login, "latest_login_token", _latest)
    monkeypatch.setattr(web_login, "accept_web_login_token", _accept)
    monkeypatch.setattr(web_login, "type_2fa_password", _type)
    monkeypatch.setattr(web_login, "fetch_account_twofa_password", _twofa)
    monkeypatch.setattr(web_login, "relaunch_account_web", _relaunch)
    return session


@pytest.mark.asyncio
async def test_no_proxy_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_proxy(monkeypatch, None)
    _patch_profile(monkeypatch, tmp_path / "acct")

    with pytest.raises(NoProxyForWebLoginError):
        await open_account_web("acct")


@pytest.mark.asyncio
async def test_first_open_accepts_the_token_once_and_finishes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    calls: dict[str, Any] = {}
    profile = tmp_path / "acct"  # does not exist yet -> first open drives login
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, profile)
    session = _wire_drive(monkeypatch, calls, states=["qr", "logged_in"])

    result = await open_account_web("acct")

    assert result.launched is True
    assert len(calls["accepted"]) == 1  # one QR token accepted, then logged in
    assert "typed" not in calls  # no password screen -> no 2FA typing
    assert "relaunch" not in calls
    assert session.closed is True  # CDP socket closed after the drive
    relay_port, launched_dir = calls["launched"]
    assert launched_dir == profile
    assert relay_port == relay.created[0].port
    assert len(relay.created) == 1


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_first_open_types_stored_password_on_the_password_screen(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, Any] = {}
    profile = tmp_path / "acct"
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, profile)
    _wire_drive(monkeypatch, calls, states=["qr", "password"], twofa="hunter2")

    result = await open_account_web("acct")

    assert result.launched is True
    assert calls["typed"] == "hunter2"  # typed exactly the stored password, once
    assert len(calls["accepted"]) == 1


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_password_screen_without_stored_password_still_launches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, Any] = {}
    profile = tmp_path / "acct"
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, profile)
    _wire_drive(monkeypatch, calls, states=["password"], token=None, twofa=None)

    result = await open_account_web("acct")

    assert result.launched is True  # operator sees the blank password screen
    assert "typed" not in calls  # nothing stored -> nothing typed


@pytest.mark.asyncio
async def test_repeat_open_relaunches_without_driving(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    calls: dict[str, Any] = {}
    profile = tmp_path / "acct"
    profile.mkdir()
    (profile / "Default").write_text("seeded", encoding="utf-8")  # non-empty -> signed in
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, profile)

    async def _launch(relay_port: int, *, profile_dir: Path) -> tuple[_FakeSession, object]:  # noqa: ARG001
        msg = "must not drive login on a repeat open"
        raise AssertionError(msg)

    _wire_drive(monkeypatch, calls, states=["logged_in"])
    monkeypatch.setattr(web_login, "launch_webk_with_hook", _launch)

    result = await open_account_web("acct")

    assert result.launched is True
    assert calls["relaunch"] == 1
    assert "launched" not in calls
    assert relay.created[0].port is not None


@pytest.mark.asyncio
async def test_second_click_reuses_the_same_relay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    calls: dict[str, Any] = {}
    profile = tmp_path / "acct"
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, profile)
    _wire_drive(monkeypatch, calls, states=["logged_in"])

    await open_account_web("acct")
    await open_account_web("acct")

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
async def test_shutdown_closes_and_clears_registered_relays(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    calls: dict[str, Any] = {}
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    _wire_drive(monkeypatch, calls, states=["logged_in"])

    await open_account_web("acct")
    assert relay.created[0].closed is False

    await web_login.shutdown_web_login_relays()

    assert relay.created[0].closed is True
    assert web_login._relays == {}
