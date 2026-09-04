"""The ``open_account_web`` orchestrator: proxy gate, mint-once, relay reuse.

Every live collaborator is faked at this module's own globals (the re-export
contract): the proxy lookup, the mint, the :class:`LocalProxyRelay`, the seeded
launch and the no-seed relaunch. The tests pin the branch logic — no proxy is
refused, a fresh profile mints and seeds exactly once, an already-signed-in
profile relaunches WITHOUT minting, a second click reuses the one relay, and a
missing stored 2FA password maps to its own domain error.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from core.telegram_client import TwoFactorRequiredError, WebLoginError
from schemas.proxy import ProxySettings
from services.accounts import web_login
from services.accounts.web_login import (
    NoProxyForWebLoginError,
    WebLoginLaunchError,
    WebLoginTwoFactorError,
    open_account_web,
)

if TYPE_CHECKING:
    from pathlib import Path

_PROXY = ProxySettings(
    proxy_type="socks5", host="proxy.example", port=1080, username="u", password="p"
)
_AUTH = object()  # opaque: the seeded launch is mocked, so its shape is irrelevant.


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the process-global relay registry + its loop-bound lock per test."""
    monkeypatch.setattr(web_login, "_relays", {})
    monkeypatch.setattr(web_login, "_relays_lock", asyncio.Lock())


class _FakeRelay:
    """Records start/close and hands out a distinct port per instance."""

    created: list[_FakeRelay] = []  # noqa: RUF012 - test double, reset per test below

    def __init__(self, upstream: ProxySettings, *, connect_timeout: float = 30.0) -> None:
        self.upstream = upstream
        self.connect_timeout = connect_timeout
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


@pytest.mark.asyncio
async def test_no_proxy_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_proxy(monkeypatch, None)
    _patch_profile(monkeypatch, tmp_path / "acct")

    with pytest.raises(NoProxyForWebLoginError):
        await open_account_web("acct")


@pytest.mark.asyncio
async def test_first_open_mints_seeds_and_starts_one_relay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    calls: dict[str, Any] = {"mint": 0, "relaunch": 0}
    profile = tmp_path / "acct"  # does not exist yet -> first open

    async def _mint(account_id: str) -> object:  # noqa: ARG001
        calls["mint"] += 1
        return _AUTH

    async def _seed(auth: object, relay_port: int, *, profile_dir: Path) -> None:
        calls["seed"] = (auth, relay_port, profile_dir)

    async def _relaunch(relay_port: int, *, profile_dir: Path) -> None:  # noqa: ARG001
        calls["relaunch"] += 1

    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, profile)
    monkeypatch.setattr(web_login, "mint_web_authorization", _mint)
    monkeypatch.setattr(web_login, "_launch_seeded_web", _seed)
    monkeypatch.setattr(web_login, "relaunch_account_web", _relaunch)

    result = await open_account_web("acct")

    assert result.launched is True
    assert calls["mint"] == 1
    assert calls["relaunch"] == 0
    auth, port, seeded_dir = calls["seed"]
    assert auth is _AUTH
    assert seeded_dir == profile
    assert len(relay.created) == 1
    assert relay.created[0].starts == 1
    assert relay.created[0].upstream is _PROXY
    assert port == relay.created[0].port


@pytest.mark.asyncio
async def test_repeat_open_relaunches_without_minting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    calls: dict[str, Any] = {"relaunch": 0}
    profile = tmp_path / "acct"
    profile.mkdir()
    (profile / "Default").write_text("seeded", encoding="utf-8")  # non-empty -> signed in

    async def _mint(account_id: str) -> object:  # noqa: ARG001
        msg = "must not mint on a repeat open"
        raise AssertionError(msg)

    async def _seed(auth: object, relay_port: int, *, profile_dir: Path) -> None:  # noqa: ARG001
        msg = "must not seed on a repeat open"
        raise AssertionError(msg)

    async def _relaunch(relay_port: int, *, profile_dir: Path) -> None:
        calls["relaunch"] += 1
        calls["relaunch_args"] = (relay_port, profile_dir)

    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, profile)
    monkeypatch.setattr(web_login, "mint_web_authorization", _mint)
    monkeypatch.setattr(web_login, "_launch_seeded_web", _seed)
    monkeypatch.setattr(web_login, "relaunch_account_web", _relaunch)

    result = await open_account_web("acct")

    assert result.launched is True
    assert calls["relaunch"] == 1
    relay_port, relaunch_dir = calls["relaunch_args"]
    assert relaunch_dir == profile
    assert relay_port == relay.created[0].port


@pytest.mark.asyncio
async def test_second_click_reuses_the_same_relay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relay: type[_FakeRelay],
) -> None:
    profile = tmp_path / "acct"

    async def _mint(account_id: str) -> object:  # noqa: ARG001
        return _AUTH

    async def _seed(auth: object, relay_port: int, *, profile_dir: Path) -> None: ...

    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, profile)
    monkeypatch.setattr(web_login, "mint_web_authorization", _mint)
    monkeypatch.setattr(web_login, "_launch_seeded_web", _seed)
    monkeypatch.setattr(web_login, "relaunch_account_web", _seed)

    await open_account_web("acct")
    await open_account_web("acct")

    assert len(relay.created) == 1  # one relay for both clicks
    assert relay.created[0].starts == 1


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_missing_two_factor_password_maps_to_its_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _mint(account_id: str) -> object:  # noqa: ARG001
        raise TwoFactorRequiredError

    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    monkeypatch.setattr(web_login, "mint_web_authorization", _mint)

    with pytest.raises(WebLoginTwoFactorError):
        await open_account_web("acct")


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_mint_failure_maps_to_launch_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _mint(account_id: str) -> object:  # noqa: ARG001
        raise WebLoginError

    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    monkeypatch.setattr(web_login, "mint_web_authorization", _mint)

    with pytest.raises(WebLoginLaunchError):
        await open_account_web("acct")


@pytest.mark.asyncio
async def test_relay_start_failure_maps_to_launch_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _DeadRelay:
        def __init__(self, upstream: ProxySettings, *, connect_timeout: float = 30.0) -> None: ...

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
    async def _mint(account_id: str) -> object:  # noqa: ARG001
        return _AUTH

    async def _seed(auth: object, relay_port: int, *, profile_dir: Path) -> None: ...

    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")
    monkeypatch.setattr(web_login, "mint_web_authorization", _mint)
    monkeypatch.setattr(web_login, "_launch_seeded_web", _seed)

    await open_account_web("acct")
    assert relay.created[0].closed is False

    await web_login.shutdown_web_login_relays()

    assert relay.created[0].closed is True
    assert web_login._relays == {}
