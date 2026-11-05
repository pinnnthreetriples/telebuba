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

The resume rule has one exception: a dedicated or shared worker that could not be dressed
is never let go, because the MTProto worker is the one that sends ``initConnection`` and an
undressed one shows Telegram the operator's real machine. The whole browser is closed
instead (:meth:`TargetDriver._end_window`) and the operator opens the window again.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import TYPE_CHECKING, cast

from core.web_login._cdp import CdpError
from core.web_login.fingerprint import (
    LocaleRejectedError,
    apply_page_fingerprint,
    worker_init_script,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from core.web_login._cdp import CdpSession
    from core.web_login.fingerprint import Fingerprint

_PAGE = "page"
# WebK's own service worker is deliberately NOT dressed. Measured on Chrome 148: a
# service_worker target paused on start answers no CDP command at all — not
# Runtime.evaluate, not Network.setUserAgentOverride — so every attempt spent the whole
# dressing deadline and resumed it undressed anyway, ~3 s on every window launch, and
# logged a warning that read like a fault. It serves WebK's media/background cache and
# its own logs; it sends no initConnection, so it is absent from Telegram's session
# record, and its requests still leave through the account's proxy. Resuming it at once
# costs nothing real. If a future Chrome starts answering, put the type back.
_WORKER_TYPES = frozenset({"worker", "shared_worker"})
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
# A PAGE that could not be dressed in that bound gets one more try with a longer one before
# it is given up on: the first command after an attach is the one most often late (the
# renderer is still starting), and a login that dies on a slow start loses the account's
# window for nothing. Workers get no retry: they are frozen one at a time on the path of
# every page load and the whole browser is closed over them.
_DRESS_RETRY_TIMEOUT = 10.0

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
        # Steps of a page's dressing already done, so a retry resumes instead of installing
        # the document-start scripts a second time.
        self._dressed_steps: dict[str, int] = {}
        self._task: asyncio.Task[None] | None = None
        self._locale_rejected: LocaleRejectedError | None = None

    async def first_page_session(self) -> str:
        """Turn on auto-attach and return the session id of the first page target.

        A locale the browser refuses ends the wait at once as :class:`LocaleRejectedError`:
        every page of this window would be refused the same way, so waiting out the
        timeout would only delay the caller's retry in another locale.
        """
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
            if self._locale_rejected is not None:
                raise self._locale_rejected
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
        resume = bool(params.get("waitingForDebugger"))
        try:
            if kind == _PAGE:
                await self._dress_page_with_retry(session_id)
                page = session_id
            elif kind in _WORKER_TYPES:
                await asyncio.wait_for(self._dress_worker(session_id), _DRESS_TIMEOUT)
        except (CdpError, OSError, TimeoutError) as exc:
            # Logged, not swallowed silently: a stall here is invisible from the page,
            # and the last one was only ever found because it was in the log.
            logger.warning("Dressing a %s target failed (%r).", kind, exc)
            if isinstance(exc, LocaleRejectedError):
                self._locale_rejected = exc
            if kind in _WORKER_TYPES:
                # Fail CLOSED, and harder than for a page: this may be the worker that
                # sends initConnection, so it is never resumed: the browser goes.
                resume = False
                await self._end_window()
                msg = f"An undressed {kind} target closed the browser."
                raise CdpError(msg) from exc
            # Fail CLOSED for a page too: an undressed page is never resumed, because
            # running it would show Telegram the operator's real machine. It is closed
            # instead, and stays paused (running nothing) if even that fails. The launch
            # then waits and refuses; a later tab simply disappears.
            resume = False
            await self._close_target(target_info)
        finally:
            self._dressed_steps.pop(session_id, None)
            if resume:
                with suppress(CdpError, OSError):
                    await self._session.send_command(
                        "Runtime.runIfWaitingForDebugger", session_id=session_id
                    )
        return page

    async def _close_target(self, target_info: object) -> None:
        """Close a page that could not be dressed; leave it paused if that fails too."""
        info = cast("dict[str, object]", target_info) if isinstance(target_info, dict) else {}
        target_id = info.get("targetId")
        if not isinstance(target_id, str):
            logger.warning("An undressed page has no target id; leaving it paused.")
            return
        try:
            await asyncio.wait_for(
                self._session.send_command("Target.closeTarget", {"targetId": target_id}),
                _DRESS_TIMEOUT,
            )
        except (CdpError, OSError, TimeoutError):
            logger.warning("An undressed page could not be closed; leaving it paused.")

    async def _end_window(self) -> None:
        """Close the whole browser rather than let an undressed worker run.

        The worker itself is left paused, so it runs nothing even if this fails. Not
        ``Target.closeTarget`` on the worker: closing is not dependable for a dedicated
        worker's target, and WebK without its MTProto worker is a broken window anyway.
        ``Browser.close`` is the command the startup sweep already relies on; the browser
        may drop the socket before it answers, so its outcome is not awaited past the
        dressing bound.
        """
        logger.warning("A worker could not be dressed; closing the browser so it never runs.")
        with suppress(CdpError, OSError, TimeoutError):
            await asyncio.wait_for(self._session.send_command("Browser.close"), _DRESS_TIMEOUT)

    async def _dress_page_with_retry(self, session_id: str) -> None:
        """Dress a page; on a failure that is not the locale's, once more with more time."""
        try:
            await asyncio.wait_for(self._dress_page(session_id), _DRESS_TIMEOUT)
        except (CdpError, OSError, TimeoutError) as exc:
            if isinstance(exc, LocaleRejectedError):
                raise
            logger.warning("Dressing a page target failed (%r); retrying once.", exc)
            await asyncio.wait_for(self._dress_page(session_id), _DRESS_RETRY_TIMEOUT)

    async def _dress_page(self, session_id: str) -> None:
        # Cascade first: a page's own dedicated workers attach through ITS session, so
        # auto-attach has to be armed there before the page runs and spawns them.
        steps = [
            self._arm_workers,
            self._apply_fingerprint,
            *(self._page_script(script) for script in self._page_scripts),
        ]
        for index, step in enumerate(steps):
            if index < self._dressed_steps.get(session_id, 0):
                continue
            await step(session_id)
            self._dressed_steps[session_id] = index + 1

    async def _arm_workers(self, session_id: str) -> None:
        await self._session.send_command(
            "Target.setAutoAttach", _AUTO_ATTACH, session_id=session_id
        )

    async def _apply_fingerprint(self, session_id: str) -> None:
        await apply_page_fingerprint(self._session, self._fingerprint, session_id=session_id)

    def _page_script(self, script: str) -> Callable[[str], Awaitable[None]]:
        async def install(session_id: str) -> None:
            await self._session.send_command(
                "Page.addScriptToEvaluateOnNewDocument", {"source": script}, session_id=session_id
            )

        return install

    async def _dress_worker(self, session_id: str) -> None:
        """Give a worker the SAME identity the page has, client hints included.

        ONE command, and it is the script: ``navigator.userAgentData`` is dressed inside
        the worker rather than over CDP. ``Emulation`` does not exist on a worker target,
        and ``Network.setUserAgentOverride`` sent to one never answers — a live run
        measured it burning the whole command timeout per worker while that worker sat
        paused on start, which cost the login its budget and the account its session.
        """
        response = await self._session.send_command(
            "Runtime.evaluate",
            {"expression": self._worker_script, "returnByValue": True},
            session_id=session_id,
        )
        # A script that threw is a worker that was NOT dressed, however politely the
        # command itself was answered.
        result = response.get("result")
        if isinstance(result, dict) and result.get("exceptionDetails"):
            msg = "The worker's identity script threw."
            raise CdpError(msg)
