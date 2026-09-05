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

Both rules are worth nothing without a clock on them, so dressing one target has its own
short deadline (:data:`_DRESS_TIMEOUT`). A CDP command that a target simply never answers
would otherwise hold that target paused for the transport's whole 30 s timeout — which is
not a hypothetical: it happened, several times over in one launch, and the login died.
"""

from __future__ import annotations

import asyncio
import logging
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
# The whole budget for dressing ONE target, and the reason it exists: the target is
# PAUSED while we dress it, so every second spent here is a second WebK is not loading.
# A CDP command against a paused target on loopback answers in milliseconds — but a
# command a target does not implement can simply never answer, and one that did cost a
# live login its session by burning ``_cdp._COMMAND_TIMEOUT`` (30 s) per worker inside
# the 90 s the drive has to reach a signed-in screen. That global timeout is right for
# what it bounds (a dead browser); this path needs its own, three orders of magnitude
# above a real round trip and still far below anything the operator would notice.
_DRESS_TIMEOUT = 3.0

logger = logging.getLogger(__name__)


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
        params = event.get("params")
        if not isinstance(params, dict):
            return None
        session_id = params.get("sessionId")
        if not isinstance(session_id, str):
            return None
        target_info = params.get("targetInfo")
        kind = target_info.get("type") if isinstance(target_info, dict) else None
        page: str | None = None
        try:
            if kind == _PAGE:
                await asyncio.wait_for(self._dress_page(session_id), _DRESS_TIMEOUT)
                page = session_id
            elif kind in _WORKER_TYPES:
                await asyncio.wait_for(self._dress_worker(session_id), _DRESS_TIMEOUT)
        except (CdpError, OSError, TimeoutError) as exc:
            # Logged, not swallowed silently: a stall here is invisible from the page,
            # and the last one was only ever found because it was in the log.
            logger.warning("Dressing a %s target failed (%r); it is resumed undressed.", kind, exc)
            # Fail CLOSED: an undressed page is NOT handed back, so the launch waits
            # and then refuses rather than navigating to Telegram wearing the
            # operator's real machine. It is still resumed below — a paused target is
            # a frozen tab — it just never becomes the window's page. Nothing to unset:
            # ``page`` only becomes non-None after the awaited call has returned.
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
        """Give a worker the SAME identity the page has, client hints included.

        ONE command, and it is the script: ``navigator.userAgentData`` is dressed inside
        the worker rather than over CDP. ``Emulation`` does not exist on a worker target,
        and ``Network.setUserAgentOverride`` sent to one never answers — a live run
        measured it burning the whole command timeout per worker while that worker sat
        paused on start, which cost the login its budget and the account its session.
        """
        await self._session.send_command(
            "Runtime.evaluate",
            {"expression": self._worker_script, "returnByValue": True},
            session_id=session_id,
        )
