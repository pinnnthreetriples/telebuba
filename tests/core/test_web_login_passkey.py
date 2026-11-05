"""The window's passkey bridge: hook installed on demand, requests signed, junk refused."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import pytest

from core.web_login import _passkey
from core.web_login._cdp import CdpError
from core.web_login._passkey import PASSKEY_HOOK, PasskeyBridge
from core.web_login.browser import launch_account_web
from schemas.passkey import WebPasskeyAssertion
from tests.core.web_login_helpers import (
    FINGERPRINT,
    PAGE,
    RecordingSession,
    attached,
    wire_launch,
)

if TYPE_CHECKING:
    from pathlib import Path

_ASSERTION = WebPasskeyAssertion(
    credential_id="Y3JlZA",
    client_data_json="Y2Q",
    authenticator_data="YWQ",
    signature="c2ln",
    user_handle="MTo1",
)


class QueueSession(RecordingSession):
    """Answers the bridge's ``take`` probe from a script, and records the rest."""

    def __init__(self, takes: list[object]) -> None:
        super().__init__()
        self._takes = list(takes)

    async def send_command(
        self,
        method: str,
        params: dict[str, object] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, object]:
        await super().send_command(method, params, session_id=session_id)
        if (params or {}).get("expression") == _passkey._TAKE_EXPR:
            value = self._takes.pop(0) if self._takes else None
            return {"result": {"result": {"value": value}}}
        return {"result": {"result": {}}}

    @property
    def answers(self) -> list[str]:
        return [
            str(p["expression"])
            for _m, p, _s in self.commands
            if str(p.get("expression", "")).startswith("window.__tbPk.")
            and p.get("expression") != _passkey._TAKE_EXPR
        ]


async def _run_bridge(session: QueueSession, sign: Any) -> None:
    bridge = PasskeyBridge(session, PAGE, sign)  # ty: ignore[invalid-argument-type]
    bridge.start()
    for _ in range(200):
        if session.answers or not session._takes:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    await bridge.aclose()


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_passkey, "_POLL_SECONDS", 0.005)


@pytest.mark.asyncio
async def test_a_queued_request_is_signed_and_handed_back_camel_cased() -> None:
    seen: list[str] = []

    async def sign(challenge: str) -> WebPasskeyAssertion:
        seen.append(challenge)
        return _ASSERTION

    session = QueueSession([{"id": 7, "challenge": "Y2hhbGxlbmdl"}])
    await _run_bridge(session, sign)

    assert seen == ["Y2hhbGxlbmdl"]
    (answer,) = session.answers
    assert answer.startswith("window.__tbPk.done(7,")
    payload = json.loads(answer.removeprefix("window.__tbPk.done(7,").removesuffix(")"))
    assert payload == {
        "credentialId": "Y3JlZA",
        "clientDataJson": "Y2Q",
        "authenticatorData": "YWQ",
        "signature": "c2ln",
        "userHandle": "MTo1",
    }
    # Evaluated on the window's page, with the result by value.
    assert {s for m, _p, s in session.commands if m == "Runtime.evaluate"} == {PAGE}


@pytest.mark.asyncio
async def test_a_signer_failure_rejects_the_page_promise() -> None:
    async def sign(_challenge: str) -> WebPasskeyAssertion:
        msg = "vault down"
        raise RuntimeError(msg)

    session = QueueSession([{"id": 3, "challenge": "abc"}])
    await _run_bridge(session, sign)

    assert session.answers == ["window.__tbPk.fail(3)"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "junk",
    [
        {"id": "7", "challenge": "abc"},
        {"id": True, "challenge": "abc"},
        {"id": 1, "challenge": "a'b"},
        {"id": 1, "challenge": ""},
        {"id": 1, "challenge": "a" * 257},
        {"id": 1},
        "challenge",
    ],
)
async def test_an_odd_request_never_reaches_the_vault(junk: object) -> None:
    called: list[str] = []

    async def sign(challenge: str) -> WebPasskeyAssertion:
        called.append(challenge)
        return _ASSERTION

    session = QueueSession([junk])
    await _run_bridge(session, sign)

    assert called == []
    assert session.answers == []


@pytest.mark.asyncio
async def test_a_dropped_probe_is_retried_not_fatal() -> None:
    class Flaky(QueueSession):
        failed = False

        async def send_command(
            self,
            method: str,
            params: dict[str, object] | None = None,
            *,
            session_id: str | None = None,
        ) -> dict[str, object]:
            if not self.failed:
                type(self).failed = True
                msg = "page reloading"
                raise CdpError(msg)
            return await super().send_command(method, params, session_id=session_id)

    async def sign(_challenge: str) -> WebPasskeyAssertion:
        return _ASSERTION

    session = Flaky([{"id": 2, "challenge": "abc"}])
    await _run_bridge(session, sign)

    assert [a.split(",")[0] for a in session.answers] == ["window.__tbPk.done(2"]


def test_the_hook_takes_over_only_telegram_requests_and_keeps_the_native_shape() -> None:
    assert "rpId === 'telegram.org'" in PASSKEY_HOOK
    # ...and only on Telegram Web's own origin: any other page gets the browser's own.
    assert "location.origin === 'https://web.telegram.org'" in PASSKEY_HOOK
    # Everything else falls through to the browser's own implementation.
    assert "real.apply(this, arguments)" in PASSKEY_HOOK
    # Disguised like the other hooks: it reports itself native and is named ``get``.
    assert "}, 'get', 0)" in PASSKEY_HOOK
    # Read from Python, so it must be reachable but not enumerable on ``window``.
    assert "Object.defineProperty(window, '__tbPk'" in PASSKEY_HOOK


async def _launch(
    monkeypatch: pytest.MonkeyPatch, profile: Path, *, signer: Any
) -> tuple[RecordingSession, Any]:
    session = RecordingSession(events=[attached(PAGE, "page")])
    wire_launch(monkeypatch, {}, session)
    window = await launch_account_web(
        41000,
        profile_dir=profile,
        fingerprint=FINGERPRINT,
        capture_tokens=False,
        passkey_signer=signer,
    )
    return session, window


def _sources(session: RecordingSession) -> list[object]:
    return [p.get("source") for m, p, _s in session.commands if m.endswith("OnNewDocument")]


@pytest.mark.asyncio
async def test_launch_installs_the_hook_and_bridge_only_with_a_signer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def sign(_challenge: str) -> WebPasskeyAssertion:
        return _ASSERTION

    with_signer, window = await _launch(monkeypatch, tmp_path / "a", signer=sign)
    try:
        assert PASSKEY_HOOK in _sources(with_signer)
        assert window.passkey is not None
    finally:
        await window.aclose()
    assert window.passkey._task is None  # the bridge stops with the window

    without, plain = await _launch(monkeypatch, tmp_path / "b", signer=None)
    await plain.aclose()
    assert PASSKEY_HOOK not in _sources(without)
    assert plain.passkey is None
