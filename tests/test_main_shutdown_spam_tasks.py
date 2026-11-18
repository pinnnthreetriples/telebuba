"""One failing @SpamBot task must not skip the next one, nor the pool and database close (F-026).

The survival probes, the history sweep, the appeal re-check and the idle re-check used to stop
inside one wrapper, so the first to raise skipped the rest. Each stops in its own guarded step.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

import main

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_SPAM_STOPS = (
    "shutdown_survival_probes",
    "shutdown_history_sweep",
    "shutdown_appeal_recheck",
    "shutdown_idle_recheck",
)
_STARTS = (
    "start_survival_probes",
    "start_history_sweep",
    "start_appeal_recheck",
    "start_idle_recheck",
)
# Everything else the lifespan runs: recorded too, so "the pool and the clients still closed"
# is asserted by name.
_REST = (
    "close_orphaned_web_windows",
    "stop_orphaned_pods",
    "start_notifications",
    "reconcile_warming_runtime",
    "reconcile_neurocomment_on_startup",
    "reconcile_neuroshilling_on_startup",
    "reconcile_chat_broadcast_on_startup",
    "reconcile_bulk_messages_on_startup",
    "start_lead_queue_on_startup",
    "reconcile_masstagging_on_startup",
    "reconcile_user_parser_on_startup",
    "reconcile_inboxes_on_startup",
    "start_scheduled_posts",
    "start_session_guard",
    "start_pool_refill",
    "shutdown_scheduled_posts",
    "shutdown_session_guard",
    "shutdown_pool_refill",
    "shutdown_warming_runtime",
    "shutdown_mobile_pods",
    "shutdown_neurocomment_on_shutdown",
    "shutdown_neuroshilling_on_shutdown",
    "shutdown_chat_broadcast_on_shutdown",
    "shutdown_lead_queue_on_shutdown",
    "shutdown_bulk_messages_on_shutdown",
    "shutdown_masstagging_on_shutdown",
    "shutdown_user_parser_on_shutdown",
    "shutdown_inbox_runtime",
    "shutdown_qr_logins",
    "shutdown_telegram_pool",
    "drain_signals",
    "shutdown_notifications",
    "shutdown_web_login",
    "close_gemini_client",
    "close_openai_client",
)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    ran: list[str] = []
    events: list[tuple[str, dict[str, object]]] = []

    async def _noop() -> None:
        return None

    async def _forever() -> None:
        await asyncio.Event().wait()

    async def _log(
        _level: str,
        event: str,
        account_id: str | None = None,  # noqa: ARG001 - mirrors log_event's signature.
        extra: dict[str, object] | None = None,
    ) -> None:
        events.append((event, extra or {}))

    monkeypatch.setattr(main, "setup_logging", lambda: None)
    monkeypatch.setattr(main, "install_signal_sink", lambda: None)
    monkeypatch.setattr(main, "log_event", _log)
    monkeypatch.setattr(main, "_log_app_started", _noop)
    monkeypatch.setattr(main, "seed_admin_if_empty", _noop)
    monkeypatch.setattr(main, "cleanup_stale_uploads", _noop)
    monkeypatch.setattr(main, "prefetch_phone_files", lambda: None)
    monkeypatch.setattr(main, "run_db_maintenance_loop", _forever)

    def _step(name: str, raises: BaseException | None = None) -> Callable[[], Awaitable[None]]:
        async def _run() -> None:
            ran.append(name)
            if raises is not None:
                raise raises

        return _run

    for name in (*_SPAM_STOPS, *_STARTS, *_REST):
        monkeypatch.setattr(main, name, _step(name))

    def _fail(name: str) -> None:
        monkeypatch.setattr(main, name, _step(name, RuntimeError("boom")))

    return {"ran": ran, "events": events, "fail": _fail}


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", _SPAM_STOPS)
async def test_one_failing_spam_task_stop_skips_neither_the_others_nor_the_pool(
    harness: dict[str, Any], failing: str
) -> None:
    harness["fail"](failing)
    async with main.lifespan(main.app):
        pass
    ran = harness["ran"]
    for stop in _SPAM_STOPS:
        assert stop in ran, f"{stop} was skipped after {failing} failed"
    for name in ("shutdown_telegram_pool", "close_gemini_client", "close_openai_client"):
        assert name in ran
    assert [event for event, _extra in harness["events"]] == ["app_shutdown_step_failed"]
    assert harness["events"][0][1]["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_the_spam_tasks_all_start_and_stop_before_the_pool_goes(
    harness: dict[str, Any],
) -> None:
    async with main.lifespan(main.app):
        assert all(start in harness["ran"] for start in _STARTS)
    ran = harness["ran"]
    pool = ran.index("shutdown_telegram_pool")
    assert all(ran.index(stop) < pool for stop in _SPAM_STOPS)
