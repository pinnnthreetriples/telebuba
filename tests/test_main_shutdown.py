"""Shutdown must not be abandoned by its first failure (carve-out #9).

The lifespan's ``finally`` was a bare sequence of ``await``s, so the first one to
raise skipped every later step — leaving the pooled Telethon clients connected
(each holding a ``.session`` SQLite file open) and the Gemini / OpenAI / Telemetr
HTTP clients unclosed. Each step is guarded now, and the sequence is preserved:
warming drains BEFORE the pool is torn down, or live ``execute(...)`` calls blow up
mid-handshake.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

import main

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_STEPS = (
    "shutdown_warming_runtime",
    "shutdown_neurocomment_on_shutdown",
    "shutdown_telegram_pool",
    "close_gemini_client",
    "close_openai_client",
    "close_telemetr_client",
)
_EXPECTED_ORDER = [
    "shutdown_warming_runtime",
    "shutdown_neurocomment_on_shutdown",
    "shutdown_telegram_pool",
    "close_gemini_client",
    "close_openai_client",
    "close_telemetr_client",
]
_LEAK_CANARY = "socks5://operator:hunter2@198.51.100.7:1080"


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Neutralise startup, record teardown, and capture the events ``main`` logs."""
    ran: list[str] = []
    events: list[tuple[str, str, dict[str, object]]] = []

    async def _noop() -> None:
        return None

    async def _forever() -> None:
        await asyncio.Event().wait()

    async def _log(
        level: str,
        event: str,
        account_id: str | None = None,  # noqa: ARG001 - mirrors log_event's signature.
        extra: dict[str, object] | None = None,
    ) -> None:
        events.append((level, event, extra or {}))

    monkeypatch.setattr(main, "setup_logging", lambda: None)
    monkeypatch.setattr(main, "log_event", _log)
    monkeypatch.setattr(main, "_log_app_started", _noop)
    monkeypatch.setattr(main, "seed_admin_if_empty", _noop)
    monkeypatch.setattr(main, "run_db_maintenance_loop", _forever)
    monkeypatch.setattr(main, "reconcile_warming_runtime", _noop)
    monkeypatch.setattr(main, "reconcile_neurocomment_on_startup", _noop)

    def _step(name: str, raises: BaseException | None = None) -> Callable[[], Awaitable[None]]:
        async def _run() -> None:
            ran.append(name)
            if raises is not None:
                raise raises

        return _run

    for name in _STEPS:
        monkeypatch.setattr(main, name, _step(name))

    def _make_fail(name: str, exc: BaseException) -> None:
        monkeypatch.setattr(main, name, _step(name, exc))

    return {"ran": ran, "events": events, "fail": _make_fail}


@pytest.mark.asyncio
async def test_a_clean_shutdown_runs_every_step_in_order(harness: dict[str, Any]) -> None:
    async with main.lifespan(main.app):
        pass
    assert harness["ran"] == _EXPECTED_ORDER
    assert harness["events"] == []


@pytest.mark.parametrize("failing", _STEPS)
@pytest.mark.asyncio
async def test_one_failing_step_never_skips_the_others(
    harness: dict[str, Any],
    failing: str,
) -> None:
    harness["fail"](failing, RuntimeError("boom"))
    async with main.lifespan(main.app):
        pass
    assert harness["ran"] == _EXPECTED_ORDER


@pytest.mark.asyncio
async def test_the_full_text_reaches_the_stdlib_sink(
    harness: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Swallowing with no sink at all makes a shutdown failure undiagnosable.

    The repo's pattern (``lifecycle.remove_account``) is BOTH: the full text to the
    stdlib logger, the type only to the structured feed. Shutdown is not reachable
    by an outsider — contrast ``services.health``, where the text is dropped for
    exactly that reason — so here it is safe to keep, and this is the only place it
    exists at all.
    """
    harness["fail"]("close_openai_client", RuntimeError("connector still draining"))
    with caplog.at_level("ERROR", logger="main"):
        async with main.lifespan(main.app):
            pass

    records = [r for r in caplog.records if r.name == "main"]
    assert len(records) == 1
    assert records[0].exc_info is not None
    assert "connector still draining" in caplog.text


@pytest.mark.asyncio
async def test_the_failure_is_reported_by_type_and_never_by_text(
    harness: dict[str, Any],
) -> None:
    """A gateway's ``str(exc)`` can carry a proxy URL with credentials or a session path."""
    harness["fail"]("shutdown_telegram_pool", RuntimeError(f"pool teardown failed: {_LEAK_CANARY}"))
    async with main.lifespan(main.app):
        pass
    assert harness["events"] == [
        (
            "ERROR",
            "app_shutdown_step_failed",
            {"step": "telegram_pool", "error_type": "RuntimeError"},
        ),
    ]
    assert "hunter2" not in str(harness["events"])
