"""Two accounts opening a window at the same time: ports, and what may run at once.

Split from ``test_web_login_browser`` to keep both files inside the 700-line test
limit; these are the cases that only exist because different accounts open
concurrently, which is the режим the whole locking story is about.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from core.web_login import browser
from core.web_login.browser import launch_account_web
from tests.core.web_login_helpers import (
    FINGERPRINT,
    PAGE,
    FakeProc,
    RecordingSession,
    attached,
    wire_launch,
)

if TYPE_CHECKING:
    from pathlib import Path

_FINGERPRINT = FINGERPRINT


def test_the_port_pick_re_rolls_off_a_port_another_launch_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two accounts opening at once must not be handed the same debug port.

    ``_free_port`` binds :0 and closes again, so the OS is free to hand the same port
    to the next caller until Chrome has actually claimed it. The loser then attaches to
    the OTHER account's browser, re-dresses its page with the wrong fingerprint and
    navigates its window. Serialising the whole 20 s DevTools wait would prevent that
    too, at the price of every other account's open; the in-flight set does not.
    """
    monkeypatch.setattr(browser, "_ports_in_flight", set())
    handed = iter([5555, 5555, 5556])
    monkeypatch.setattr(browser, "_free_port", lambda: next(handed))

    first = browser._pick_port()
    second = browser._pick_port()

    assert (first, second) == (5555, 5556)


@pytest.mark.asyncio
@pytest.mark.parametrize("boom", [NotImplementedError, FileNotFoundError, OSError])
async def test_a_spawn_that_never_returns_releases_its_port_reservation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    boom: type[BaseException],
) -> None:
    """The reservation was released in a ``finally`` attached to the wait, not the spawn.

    A spawn that raises never reaches that wait, so its port stayed reserved for the
    life of the process — and ``NotImplementedError`` is not a rare case: on a Windows
    SelectorEventLoop (``uvicorn --reload``) EVERY click fails exactly here, burning a
    port each time until ``_pick_port`` runs out of re-rolls. ``FileNotFoundError`` (a
    browser removed between the lookup and the spawn) and an ``OSError`` under handle
    pressure leak the same way. The existing ``--reload`` test patches
    ``launch_account_web`` wholesale, so it never enters this path at all.
    """
    recorder: dict[str, Any] = {}
    wire_launch(monkeypatch, recorder, RecordingSession())

    async def _dead_exec(*_args: object, **_kwargs: object) -> None:
        raise boom

    monkeypatch.setattr(browser.asyncio, "create_subprocess_exec", _dead_exec)

    with pytest.raises(boom):
        await launch_account_web(
            41000,
            profile_dir=tmp_path / "acct-1",
            fingerprint=_FINGERPRINT,
            capture_tokens=True,
        )

    assert browser._ports_in_flight == set()


@pytest.mark.asyncio
async def test_the_spawns_themselves_are_not_serialized_across_accounts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Starting Chrome is the slow part, and no lock may hold it one at a time.

    ``_pick_port`` reserves synchronously, so on one event loop nothing can interleave
    between its check and its add and no lock is needed to keep two launches off one
    port. A lock around the spawn as well bought no guarantee and charged every later
    account a full Chrome start: measured on a real browser, three simultaneous opens
    went from 0.7 s each to 2.0-2.4 s each.
    """
    recorder: dict[str, Any] = {}
    wire_launch(monkeypatch, recorder, RecordingSession())
    ports = iter(range(5555, 5600))
    monkeypatch.setattr(browser, "_free_port", lambda: next(ports))
    overlap = {"live": 0, "max": 0}

    class _FreshCdp:
        @staticmethod
        async def connect(_ws_url: str) -> RecordingSession:
            return RecordingSession(events=[attached(PAGE, "page")])

    monkeypatch.setattr(browser, "CdpSession", _FreshCdp)

    async def _slow_spawn(_program: str, *_args: str, **_kwargs: object) -> FakeProc:
        overlap["live"] += 1
        overlap["max"] = max(overlap["max"], overlap["live"])
        await asyncio.sleep(0.02)
        overlap["live"] -= 1
        return FakeProc(recorder)

    monkeypatch.setattr(browser.asyncio, "create_subprocess_exec", _slow_spawn)

    windows = await asyncio.gather(
        *(
            launch_account_web(
                41000 + n,
                profile_dir=tmp_path / f"acct-{n}",
                fingerprint=_FINGERPRINT,
                capture_tokens=False,
            )
            for n in range(3)
        )
    )
    for window in windows:
        await window.driver.aclose()

    assert overlap["max"] == 3  # all three Chromes started at once, not in a queue


@pytest.mark.asyncio
async def test_the_devtools_wait_is_not_serialized_across_accounts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """One slow Chrome start must not delay every other account's open by up to 20 s.

    The port reservation is what keeps two launches off one port; waiting for the
    DevTools endpoint under a lock would turn a single wedged browser into a
    process-wide stall.
    """
    recorder: dict[str, Any] = {}
    wire_launch(monkeypatch, recorder, RecordingSession())
    ports = iter(range(5555, 5600))
    monkeypatch.setattr(browser, "_free_port", lambda: next(ports))
    overlap = {"live": 0, "max": 0}

    class _FreshCdp:
        """One socket per launch — two concurrent opens are two separate browsers."""

        @staticmethod
        async def connect(_ws_url: str) -> RecordingSession:
            return RecordingSession(events=[attached(PAGE, "page")])

    monkeypatch.setattr(browser, "CdpSession", _FreshCdp)

    ws_ports: list[int] = []

    async def _slow_ws(port: int, _process: object, _profile: Path) -> str:
        ws_ports.append(port)
        overlap["live"] += 1
        overlap["max"] = max(overlap["max"], overlap["live"])
        await asyncio.sleep(0.02)
        overlap["live"] -= 1
        return "ws://127.0.0.1:5555/devtools/browser/ABC"

    monkeypatch.setattr(browser, "_browser_ws", _slow_ws)

    windows = await asyncio.gather(
        *(
            launch_account_web(
                41000 + n,
                profile_dir=tmp_path / f"acct-{n}",
                fingerprint=_FINGERPRINT,
                capture_tokens=False,
            )
            for n in range(2)
        )
    )
    for window in windows:
        await window.driver.aclose()

    assert overlap["max"] == 2  # both waits ran at once
    # ...and they were still handed different ports, which is what the lock is for.
    assert len({int(port) for port in ws_ports}) == 2
