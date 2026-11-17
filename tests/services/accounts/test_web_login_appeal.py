"""Opening the account's window on @SpamBot for the appeal: a navigation, and nothing else.

The hard limit (docs/spam-intel.md 5.12, T18 (b)): a human does the whole appeal in the window.
Here the REAL launcher and the REAL driver run against a fake DevTools socket that records every
command, and the appeal's open is compared with a plain open: the same commands, the same
identity, one navigation to a different address, no input of any kind.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import pytest

from core.config import settings
from core.web_login import browser, navigate_window, spambot_dialog_url
from core.web_login._cdp import CdpError
from core.web_login._page import _PAGE_STATE_EXPR
from services.accounts import _web_target, web_login
from services.accounts.web_login import NoProxyForWebLoginError, open_account_web
from tests.core.web_login_helpers import PAGE, FakeProc, RecordingSession, attached, wire_launch
from tests.services.accounts._web_login_support import PROXY as _PROXY
from tests.services.accounts._web_login_support import FakeRelay as _FakeRelay
from tests.services.accounts._web_login_support import fresh_registry, relay
from tests.services.accounts._web_login_support import patch_profile as _patch_profile
from tests.services.accounts._web_login_support import patch_proxy as _patch_proxy
from tests.services.accounts._web_login_support import seeded as _seeded

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["fresh_registry", "relay"]

_HASH_URL = "https://web.telegram.org/k/#@SpamBot"
_TGADDR_URL = "https://web.telegram.org/k/?tgaddr=tg%3A%2F%2Fresolve%3Fdomain%3DSpamBot"
_START_URL = "https://web.telegram.org/k/"
# Everything the launch needs to dress and start a window, plus the two commands the appeal adds
# to an open one. ``Runtime.evaluate`` is the sign-in READ and is checked separately below;
# ``Runtime.runIfWaitingForDebugger`` lets a paused page start once it is dressed.
_ALLOWED = frozenset(
    {
        "Target.setAutoAttach",
        "Target.activateTarget",
        "Emulation.setUserAgentOverride",
        "Emulation.setTimezoneOverride",
        "Emulation.setLocaleOverride",
        "Page.enable",
        "Page.addScriptToEvaluateOnNewDocument",
        "Page.bringToFront",
        "Page.navigate",
        "Runtime.evaluate",
        "Runtime.runIfWaitingForDebugger",
    }
)
_SIGNED_IN = json.dumps({"auth": False, "pw": False, "qr": False, "app": True})


class HeldSession(RecordingSession):
    """The recording socket, held open like a real window's until the test ends it."""

    def __init__(self) -> None:
        super().__init__(
            evaluate_values={_PAGE_STATE_EXPR: _SIGNED_IN}, events=[attached(PAGE, "page")]
        )
        self._dropped = asyncio.Event()

    async def wait_closed(self) -> None:
        await self._dropped.wait()

    async def aclose(self) -> None:
        await super().aclose()
        self._dropped.set()


class HeldProc(FakeProc):
    """A browser that stays up until it is killed."""

    def __init__(self, recorder: dict[str, Any]) -> None:
        super().__init__(recorder)
        self._gone = asyncio.Event()

    def kill(self) -> None:
        super().kill()
        self._gone.set()

    async def wait(self) -> int:
        await self._gone.wait()
        return 0


@pytest.fixture
def window_session(monkeypatch: pytest.MonkeyPatch) -> HeldSession:
    """The real launcher over a fake socket; the registry and the proxy are the test's."""
    session = HeldSession()
    recorder: dict[str, Any] = {}
    wire_launch(monkeypatch, recorder, session, proc=HeldProc(recorder))
    return session


@pytest.fixture
def signed_in_account(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """An account with a proxy and a profile a login has completed in (nothing to drive)."""
    profile = _seeded(tmp_path / "acct")
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, profile)

    async def _no_signer(_account_id: str) -> None:
        return None

    monkeypatch.setattr(web_login, "web_passkey_signer", _no_signer)
    return profile


def _navigations(session: RecordingSession) -> list[str]:
    return [str(p["url"]) for m, p, _s in session.commands if m == "Page.navigate"]


def _reads(session: RecordingSession) -> list[str]:
    return [str(p["expression"]) for m, p, _s in session.commands if m == "Runtime.evaluate"]


async def _end() -> None:
    await web_login.shutdown_web_login()


@pytest.mark.usefixtures("relay", "signed_in_account")
@pytest.mark.asyncio
async def test_a_new_window_launches_on_the_dialog_with_the_same_commands_as_a_plain_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain = HeldSession()
    wire_launch(monkeypatch, {}, plain, proc=HeldProc({}))
    assert (await open_account_web("acct")).signed_in is True
    await _end()
    web_login._closing.clear()

    appeal = HeldSession()
    wire_launch(monkeypatch, {}, appeal, proc=HeldProc({}))
    result = await open_account_web("acct", target="spambot")
    await _end()

    assert (result.launched, result.signed_in) == (True, True)
    assert _navigations(plain) == [_START_URL]
    assert _navigations(appeal) == [_HASH_URL]
    # The whole command stream is the plain open's, byte for byte, but for that one address.
    assert appeal.methods == plain.methods
    assert [(m, p) for m, p, _s in appeal.commands if m != "Page.navigate"] == [
        (m, p) for m, p, _s in plain.commands if m != "Page.navigate"
    ]


@pytest.mark.usefixtures("relay", "signed_in_account")
@pytest.mark.asyncio
async def test_the_appeal_sends_only_allowed_commands_and_no_input(
    window_session: HeldSession,
) -> None:
    await open_account_web("acct", target="spambot")
    await open_account_web("acct", target="spambot")  # the raise-and-navigate path too
    await _end()

    methods = set(window_session.methods)
    assert methods <= _ALLOWED
    assert not any(method.startswith("Input.") for method in window_session.methods)
    # The only script it ever evaluates is the page-state READ the sign-in check has always made.
    assert set(_reads(window_session)) == {_PAGE_STATE_EXPR}
    assert {"Page.navigate", "Page.bringToFront"} <= methods


@pytest.mark.usefixtures("relay", "signed_in_account")
@pytest.mark.asyncio
async def test_an_open_signed_in_window_is_raised_and_navigated_not_relaunched(
    window_session: HeldSession,
) -> None:
    await open_account_web("acct")
    assert _navigations(window_session) == [_START_URL]
    assert len(_FakeRelay.created) == 1

    again = await open_account_web("acct", target="spambot")

    assert (again.launched, again.signed_in) == (True, True)
    assert _navigations(window_session) == [_START_URL, _HASH_URL]
    assert window_session.methods.count("Page.bringToFront") == 1
    # raised first, then pointed at the dialog
    raised = window_session.methods.index("Page.bringToFront")
    assert window_session.methods.index("Page.navigate", raised) > raised
    assert len(_FakeRelay.created) == 1  # no second window, no second relay
    await _end()


@pytest.mark.usefixtures("relay", "signed_in_account")
@pytest.mark.asyncio
async def test_a_plain_second_click_still_only_raises(window_session: HeldSession) -> None:
    await open_account_web("acct")
    await open_account_web("acct")

    assert _navigations(window_session) == [_START_URL]
    assert window_session.methods.count("Page.bringToFront") == 1
    await _end()


@pytest.mark.usefixtures("relay", "signed_in_account")
@pytest.mark.asyncio
async def test_the_dialog_address_is_a_setting_not_a_code_change(
    window_session: HeldSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert settings.spam_intel.appeal_dialog_target == "hash"
    monkeypatch.setattr(settings.spam_intel, "appeal_dialog_target", "tgaddr")

    await open_account_web("acct", target="spambot")

    assert _navigations(window_session) == [_TGADDR_URL]
    await _end()


@pytest.mark.usefixtures("relay", "signed_in_account")
@pytest.mark.asyncio
async def test_a_window_that_cannot_be_navigated_is_still_the_operators_window(
    window_session: HeldSession,
) -> None:
    await open_account_web("acct")
    window_session._fail_on = "Page.navigate"

    result = await open_account_web("acct", target="spambot")

    assert (result.launched, result.signed_in) == (True, True)
    await _end()


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_the_appeal_open_is_refused_without_a_proxy_like_the_plain_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_proxy(monkeypatch, None)
    _patch_profile(monkeypatch, tmp_path / "acct")

    with pytest.raises(NoProxyForWebLoginError):
        await open_account_web("acct", target="spambot")

    assert _FakeRelay.created == []


@pytest.mark.usefixtures("relay")
@pytest.mark.asyncio
async def test_a_window_whose_login_is_being_driven_is_never_navigated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pointing a QR screen elsewhere mid-login would break the very login being driven."""
    navigated: list[str] = []

    async def _navigate(_window: object, url: str) -> None:
        navigated.append(url)

    async def _focus(_window: object) -> None:
        return None

    async def _drive(_account: str, _window: object, _profile: object) -> Any:
        from schemas.accounts import OpenWebResult  # noqa: PLC0415 - the one result it needs

        return OpenWebResult(launched=True, signed_in=False)

    class _Window:
        alive = True
        running = True

        async def kill(self) -> None:
            return None

    window = _Window()
    web_login._windows["acct"] = window  # ty: ignore[invalid-assignment]
    _patch_proxy(monkeypatch, _PROXY)
    _patch_profile(monkeypatch, tmp_path / "acct")  # never signed in: no marker
    monkeypatch.setattr(_web_target, "navigate_window", _navigate)
    monkeypatch.setattr(web_login, "focus_window", _focus)
    monkeypatch.setattr(web_login, "_drive_and_mark", _drive)

    result = await open_account_web("acct", target="spambot")

    assert result.signed_in is False
    assert navigated == []


def test_the_dialog_address_table_has_the_two_candidates_and_a_safe_default() -> None:
    assert spambot_dialog_url("hash") == _HASH_URL
    assert spambot_dialog_url("tgaddr") == _TGADDR_URL
    assert spambot_dialog_url("anything else") == _HASH_URL


@pytest.mark.asyncio
async def test_navigate_window_is_one_page_navigate_on_the_windows_page() -> None:
    session = RecordingSession()
    window = browser.WebWindow(
        session=session,  # ty: ignore[invalid-argument-type]
        driver=None,  # ty: ignore[invalid-argument-type]
        page=PAGE,
        process=FakeProc({}),  # ty: ignore[invalid-argument-type]
    )

    await navigate_window(window, _HASH_URL)

    assert session.commands == [("Page.navigate", {"url": _HASH_URL}, PAGE)]


@pytest.mark.asyncio
async def test_a_navigation_failure_surfaces_as_a_cdp_error_for_the_service_to_absorb() -> None:
    session = RecordingSession(fail_on="Page.navigate")
    window = browser.WebWindow(
        session=session,  # ty: ignore[invalid-argument-type]
        driver=None,  # ty: ignore[invalid-argument-type]
        page=PAGE,
        process=FakeProc({}),  # ty: ignore[invalid-argument-type]
    )

    with pytest.raises(CdpError):
        await navigate_window(window, _HASH_URL)
