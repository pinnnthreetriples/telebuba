"""Keep every target of one launched browser wearing the account's fingerprint.

``Target.setAutoAttach`` with ``waitForDebuggerOnStart`` hands us each new page and
worker paused, before a line of its script has run. That pause is the whole point:
WebK builds ``initConnection`` inside its MTProto worker, and a page-level override
never crosses into a worker, so the identity has to be installed on the worker's own
session while it is still frozen.

New targets keep appearing for as long as the window lives — every reload spawns a
fresh worker, and the operator can open another tab — so :meth:`TargetDriver.start`
leaves a task running that dresses each one as it attaches. Two rules matter for the
operator: a paused target is a hung tab, so every attach is resumed even when dressing
it failed; and dressing failures are swallowed rather than killing the driver, because
a dead driver would freeze the next tab the operator opens.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from core.web_login._cdp import CdpError
from core.web_login.fingerprint import apply_page_fingerprint, worker_init_script

if TYPE_CHECKING:
    from core.web_login._cdp import CdpSession
    from core.web_login.fingerprint import Fingerprint

_PAGE = "page"
_WORKER_TYPES = frozenset({"worker", "shared_worker", "service_worker"})
_ATTACHED = "Target.attachedToTarget"
_AUTO_ATTACH: dict[str, object] = {
    "autoAttach": True,
    "waitForDebuggerOnStart": True,
    "flatten": True,
}
# How long to wait for the browser to attach its first page, and the idle poll the
# background driver parks on between attaches.
_FIRST_PAGE_TIMEOUT = 20.0
_EVENT_POLL = 1.0


class TargetDriver:
    """Dresses every page/worker of one browser in ``fingerprint`` as it attaches."""

    def __init__(
        self,
        session: CdpSession,
        fingerprint: Fingerprint,
        *,
        page_scripts: tuple[str, ...] = (),
    ) -> None:
        self._session = session
        self._fingerprint = fingerprint
        self._page_scripts = page_scripts
        self._worker_script = worker_init_script(fingerprint)
        self._task: asyncio.Task[None] | None = None

    async def first_page_session(self) -> str:
        """Turn on auto-attach and return the session id of the first page target."""
        await self._session.send_command("Target.setAutoAttach", _AUTO_ATTACH)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _FIRST_PAGE_TIMEOUT
        while loop.time() < deadline:
            event = await self._session.next_target_event(_EVENT_POLL)
            if event is None:
                continue
            page = await self._handle(event)
            if page is not None:
                return page
        msg = "Browser never attached a page target."
        raise CdpError(msg)

    def start(self) -> None:
        """Keep dressing targets in the background for the window's whole life."""
        self._task = asyncio.create_task(self._run())

    async def aclose(self) -> None:
        """Stop the background driver (the browser itself is left alone)."""
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _run(self) -> None:
        while not self._session.closed:
            event = await self._session.next_target_event(_EVENT_POLL)
            if event is None:
                continue
            with suppress(CdpError, OSError):
                await self._handle(event)

    async def _handle(self, event: dict) -> str | None:
        """Dress one attached target; return its id when it is a page, else ``None``."""
        if event.get("method") != _ATTACHED:
            return None
        params = event.get("params", {})
        session_id = params.get("sessionId")
        kind = params.get("targetInfo", {}).get("type")
        if not isinstance(session_id, str):
            return None
        page: str | None = None
        try:
            if kind == _PAGE:
                await self._dress_page(session_id)
                page = session_id
            elif kind in _WORKER_TYPES:
                await self._dress_worker(session_id)
        except (CdpError, OSError):
            # Fail CLOSED: an undressed page is NOT handed back, so the launch waits
            # and then refuses rather than navigating to Telegram wearing the
            # operator's real machine. It is still resumed below — a paused target is
            # a frozen tab — it just never becomes the window's page.
            page = None
        finally:
            if params.get("waitingForDebugger"):
                with suppress(CdpError, OSError):
                    await self._session.send_command(
                        "Runtime.runIfWaitingForDebugger", session_id=session_id
                    )
        return page

    async def _dress_page(self, session_id: str) -> None:
        # Cascade first: a page's own dedicated workers attach through ITS session, so
        # auto-attach has to be armed there before the page runs and spawns them.
        await self._session.send_command(
            "Target.setAutoAttach", _AUTO_ATTACH, session_id=session_id
        )
        await apply_page_fingerprint(self._session, self._fingerprint, session_id=session_id)
        for script in self._page_scripts:
            await self._session.send_command(
                "Page.addScriptToEvaluateOnNewDocument", {"source": script}, session_id=session_id
            )

    async def _dress_worker(self, session_id: str) -> None:
        await self._session.send_command(
            "Runtime.evaluate",
            {"expression": self._worker_script, "returnByValue": True},
            session_id=session_id,
        )
