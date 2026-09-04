"""The target driver: every page and worker of a window wears the account identity.

The worker half is the point of the whole mechanism — WebK builds ``initConnection``
inside its MTProto worker, which a page-level override never reaches — so these tests
pin that a worker target is injected on its OWN session, and that a target is ALWAYS
resumed afterwards, including when dressing it failed. A target left paused is a tab
that never loads for the operator.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from core.web_login._cdp import CdpError
from core.web_login._targets import TargetDriver
from core.web_login.fingerprint import fingerprint_for, worker_init_script

_FINGERPRINT = fingerprint_for("acct-1", "DE")
_RESUME = "Runtime.runIfWaitingForDebugger"


def attached(session_id: str, kind: str, *, waiting: bool = True) -> dict[str, Any]:
    return {
        "method": "Target.attachedToTarget",
        "params": {
            "sessionId": session_id,
            "targetInfo": {"type": kind, "targetId": session_id},
            "waitingForDebugger": waiting,
        },
    }


class _Session:
    def __init__(
        self,
        events: list[dict[str, Any]] | None = None,
        fail_methods: frozenset[str] = frozenset(),
    ) -> None:
        self.commands: list[tuple[str, dict[str, object], str | None]] = []
        self._events = list(events or [])
        self._fail = fail_methods
        self.closed = False

    async def send_command(
        self,
        method: str,
        params: dict[str, object] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, object]:
        if method in self._fail:
            msg = f"{method} refused"
            raise CdpError(msg)
        self.commands.append((method, params or {}, session_id))
        return {"result": {}}

    async def next_target_event(self, wait_seconds: float) -> dict[str, Any] | None:
        if self._events:
            return self._events.pop(0)
        await asyncio.sleep(min(wait_seconds, 0.02))
        return None

    def sent(self, method: str) -> list[tuple[dict[str, object], str | None]]:
        return [(p, s) for m, p, s in self.commands if m == method]


def _driver(session: _Session, **kwargs: Any) -> TargetDriver:
    return TargetDriver(session, _FINGERPRINT, **kwargs)  # ty: ignore[invalid-argument-type]


@pytest.mark.asyncio
async def test_first_page_session_arms_auto_attach_and_returns_the_page() -> None:
    session = _Session(events=[attached("P1", "page")])

    page = await _driver(session).first_page_session()

    assert page == "P1"
    auto = session.sent("Target.setAutoAttach")
    # Armed at browser level, then cascaded onto the page so ITS workers pause too.
    assert auto[0][1] is None
    assert auto[1][1] == "P1"
    assert all(params["waitForDebuggerOnStart"] is True for params, _s in auto)
    assert session.sent(_RESUME)[0][1] == "P1"


@pytest.mark.asyncio
async def test_a_worker_is_injected_on_its_own_session_then_resumed() -> None:
    session = _Session(events=[attached("W1", "shared_worker"), attached("P1", "page")])

    await _driver(session).first_page_session()

    injected = [
        (params, target) for params, target in session.sent("Runtime.evaluate") if target == "W1"
    ]
    assert injected, "the worker was never dressed"
    assert injected[0][0]["expression"] == worker_init_script(_FINGERPRINT)
    # Injected BEFORE the worker was let go, or its script would already have run.
    methods = [m for m, _p, s in session.commands if s == "W1"]
    assert methods.index("Runtime.evaluate") < methods.index(_RESUME)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["worker", "shared_worker", "service_worker"])
async def test_every_worker_flavour_is_dressed(kind: str) -> None:
    session = _Session(events=[attached("W1", kind), attached("P1", "page")])

    await _driver(session).first_page_session()

    assert any(target == "W1" for _p, target in session.sent("Runtime.evaluate"))


@pytest.mark.asyncio
async def test_a_target_is_resumed_even_when_dressing_it_failed() -> None:
    session = _Session(
        events=[attached("W1", "worker"), attached("P1", "page")],
        fail_methods=frozenset({"Runtime.evaluate"}),
    )

    page = await _driver(session).first_page_session()

    assert page == "P1"
    # The worker was never dressed, but it MUST still be let go, or it hangs.
    assert ({}, "W1") in [(p, s) for p, s in session.sent(_RESUME)]


@pytest.mark.asyncio
async def test_a_target_that_is_not_waiting_is_not_resumed() -> None:
    session = _Session(events=[attached("P1", "page", waiting=False)])

    await _driver(session).first_page_session()

    assert session.sent(_RESUME) == []


@pytest.mark.asyncio
async def test_page_scripts_are_installed_on_the_page() -> None:
    session = _Session(events=[attached("P1", "page")])

    await _driver(session, page_scripts=("window.__x=1;",)).first_page_session()

    sources = [p.get("source") for p, _s in session.sent("Page.addScriptToEvaluateOnNewDocument")]
    assert "window.__x=1;" in sources


@pytest.mark.asyncio
async def test_the_background_driver_dresses_workers_that_appear_later() -> None:
    session = _Session(events=[attached("P1", "page")])
    driver = _driver(session)
    await driver.first_page_session()
    driver.start()
    try:
        # A reload spawns a fresh worker long after the launch call returned.
        session._events.append(attached("W2", "worker"))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if any(target == "W2" for _p, target in session.sent("Runtime.evaluate")):
                break
    finally:
        await driver.aclose()

    assert any(target == "W2" for _p, target in session.sent("Runtime.evaluate"))


@pytest.mark.asyncio
async def test_first_page_session_raises_when_no_page_ever_attaches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.web_login._targets._FIRST_PAGE_TIMEOUT", 0.05)
    session = _Session(events=[attached("W1", "worker")])

    with pytest.raises(CdpError):
        await _driver(session).first_page_session()
