"""Tests for ``core.logging`` — the three-tier logging gateway."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from core import events
from core import logging as logging_module
from core.config import settings
from core.db import configure_database, list_recent_logs
from core.logging import log_event, reset_logging_for_tests, setup_logging, signal_event

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_logging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "rotation", "1 MB")
    monkeypatch.setattr(settings.logging, "retention", 2)
    monkeypatch.setattr(settings.logging, "level", "INFO")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    configure_database(tmp_path / "logs.db")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


@pytest.mark.asyncio
async def test_info_writes_row_with_success_status() -> None:
    await log_event("INFO", "account_login", account_id="acc-1", extra={"ip": "1.2.3.4"})

    rows = await list_recent_logs(limit=5)
    assert len(rows) == 1
    row = rows[0]
    assert row.level == "INFO"
    assert row.status == "success"
    assert row.account_id == "acc-1"
    assert row.event == "account_login"
    assert row.extra == {"ip": "1.2.3.4"}


@pytest.mark.asyncio
async def test_warning_writes_row_with_warning_status() -> None:
    await log_event("WARNING", "flood_wait", account_id="acc-2", extra={"seconds": 30})

    rows = await list_recent_logs(limit=5)
    assert rows[0].level == "WARNING"
    assert rows[0].status == "warning"


@pytest.mark.asyncio
async def test_error_writes_row_with_error_status() -> None:
    await log_event("ERROR", "banned", account_id="acc-3", extra={"reason": "spam"})

    rows = await list_recent_logs(limit=5)
    assert rows[0].level == "ERROR"
    assert rows[0].status == "error"


@pytest.mark.asyncio
async def test_extra_defaults_to_empty_dict() -> None:
    await log_event("INFO", "ping")

    rows = await list_recent_logs(limit=5)
    assert rows[0].extra == {}
    assert rows[0].account_id is None


@pytest.mark.asyncio
async def test_writes_to_loguru_file() -> None:
    await log_event("INFO", "first_event", account_id="acc-x")

    contents = settings.logging.path.read_text(encoding="utf-8")
    assert "first_event" in contents
    assert "acc-x" in contents


@pytest.mark.asyncio
async def test_stdlib_logger_text_is_retained_in_the_loguru_file() -> None:
    """The stdlib sink is durable again — and still reaches no route.

    Bounding ``extra`` moved full third-party exception text onto each module's stdlib
    logger. uvicorn's ``LOGGING_CONFIG`` has no ``root`` key, so those records fell to
    ``logging.lastResort``: nothing was dropped, but the text existed only in the
    process's stderr stream, so an operator could no longer recover why a proxy failed
    six hours ago. ``_StdlibToLoguru`` puts it back on disk — and only on disk.
    """
    secret = "socks5://leakuser:LEAKPASS123@10.9.8.7:1080"
    exc = OSError(f"proxy {secret} refused")
    # ``exc_info=`` rather than a live ``raise``: the same call shape ``api.errors`` and
    # ``_action_results`` use, and it keeps this a test about the sink, not the raise.
    logging.getLogger("core.telegram_client._pool").error("pool connect failed", exc_info=exc)

    await logging_module.logger.complete()
    contents = settings.logging.path.read_text(encoding="utf-8")
    assert "LEAKPASS123" in contents
    assert "pool connect failed" in contents
    # The retention fix must not have opened a second route: nothing was persisted to
    # the ``logs`` table, which is what ``GET /logs`` and ``GET /events`` serve.
    assert await list_recent_logs(limit=5) == []


def test_stdlib_bridge_keeps_the_console_and_prints_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Console output is unchanged, and the bridge does not duplicate it.

    Adding *any* handler to root silences ``logging.lastResort`` (it only fires when a
    record finds none), so it is re-added explicitly instead of being replaced by a
    fresh ``StreamHandler`` — reusing the handler that already fires is what makes the
    console byte-identical rather than merely similar.
    """
    root = logging.getLogger()
    assert logging.lastResort in root.handlers
    assert any(isinstance(handler, logging_module._StdlibToLoguru) for handler in root.handlers)

    logging.getLogger("core.proxy_check").warning("proxy check failed: sentinel-9f3a")

    assert capsys.readouterr().err.count("sentinel-9f3a") == 1


def test_reset_removes_the_bridge_so_fixtures_do_not_stack_handlers() -> None:
    """Every test fixture in the suite resets then re-runs ``setup_logging``.

    Without the paired removal in ``reset_logging_for_tests`` each pair would leave
    another ``(lastResort, bridge)`` on root, and every line would print N times.
    """
    root = logging.getLogger()
    before = len(root.handlers)
    reset_logging_for_tests()
    setup_logging()
    assert len(root.handlers) == before


@pytest.mark.asyncio
async def test_error_calls_sentry_when_dsn_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.logging, "sentry_dsn", "https://fake@sentry.example/0")
    reset_logging_for_tests()
    with patch("core.logging.sentry_sdk.init") as mock_init:
        setup_logging()
    assert mock_init.called

    with (
        patch("core.logging.sentry_sdk.capture_message") as mock_capture,
        patch(
            "core.logging.sentry_sdk.push_scope",
        ) as mock_scope,
    ):
        mock_scope.return_value.__enter__.return_value = MagicMock()
        await log_event("ERROR", "banned", account_id="acc-9", extra={"reason": "spam"})

    mock_capture.assert_called_once()
    args, kwargs = mock_capture.call_args
    assert "banned" in args[0]
    assert kwargs.get("level") == "error"


def test_sentry_init_never_ships_stack_frame_locals(monkeypatch: pytest.MonkeyPatch) -> None:
    """``include_local_variables=False`` — the one switch that reaches Telethon's frames.

    Its default is ``True``, and the default logging integration turns any ERROR
    record carrying ``exc_info`` into an event with every frame's ``f_locals``
    rendered by ``repr()``. A failed ``edit_2fa`` holds the cloud password, the
    recovery address and the mailed code as bare string locals inside Telethon, so
    field-level ``repr=False`` cannot close that path — only this can.
    """
    monkeypatch.setattr(settings.logging, "sentry_dsn", "https://fake@sentry.example/0")
    reset_logging_for_tests()
    with patch("core.logging.sentry_sdk.init") as mock_init:
        setup_logging()

    assert mock_init.call_args.kwargs["include_local_variables"] is False


@pytest.mark.asyncio
async def test_error_skips_sentry_when_dsn_unset() -> None:
    with patch("core.logging.sentry_sdk.capture_message") as mock_capture:
        await log_event("ERROR", "no_dsn_error", account_id="acc-8")
    mock_capture.assert_not_called()


@pytest.mark.asyncio
async def test_info_never_calls_sentry_even_with_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.logging, "sentry_dsn", "https://fake@sentry.example/0")
    reset_logging_for_tests()
    with patch("core.logging.sentry_sdk.init"):
        setup_logging()

    with patch("core.logging.sentry_sdk.capture_message") as mock_capture:
        await log_event("INFO", "routine")
    mock_capture.assert_not_called()


def test_setup_logging_is_idempotent() -> None:
    initial_initialized = logging_module._state.initialized
    setup_logging()  # already called by autouse fixture
    setup_logging()
    setup_logging()
    assert logging_module._state.initialized is True
    assert initial_initialized is True


@pytest.mark.asyncio
async def test_log_event_publishes_persisted_row_to_bus() -> None:
    async with events.subscribe() as queue:
        await log_event("WARNING", "live_event", account_id="acc-live", extra={"k": "v"})
        published = await asyncio.wait_for(queue.get(), timeout=1)
    assert published.event == "live_event"
    assert published.status == "warning"
    assert published.account_id == "acc-live"
    assert published.extra == {"k": "v"}
    # The published row is the persisted DB row (real id), not a synthetic one.
    assert published.id > 0


@pytest.mark.asyncio
async def test_signal_event_publishes_to_bus_without_persisting() -> None:
    """A transient nudge reaches live subscribers but is deliberately never persisted.

    This is the whole point of ``signal_event`` (vs ``log_event``): high-frequency
    refresh nudges (onboarding channel-joins) must refresh the SPA without flooding
    the ``logs`` table / event log.
    """
    async with events.subscribe() as queue:
        signal_event("neurocomment_onboarding_progress", extra={"k": "v"})
        published = await asyncio.wait_for(queue.get(), timeout=1)

    assert published.event == "neurocomment_onboarding_progress"
    assert published.status == "success"
    assert published.extra == {"k": "v"}
    assert published.id == 0  # synthetic, never a real DB id
    # Nothing was written to the logs table.
    assert await list_recent_logs(limit=5) == []


@pytest.mark.asyncio
async def test_publish_failure_does_not_break_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_entry: object) -> None:
        msg = "bus down"
        raise RuntimeError(msg)

    monkeypatch.setattr(logging_module, "publish_event", _boom)
    await log_event("INFO", "still_logs")  # must not raise

    rows = await list_recent_logs(limit=5)
    assert rows[0].event == "still_logs"


@pytest.mark.asyncio
async def test_extra_is_json_serialisable_round_trip() -> None:
    nested: dict[str, object] = {"nested": {"a": 1, "b": [1, 2, 3]}, "flag": True}
    await log_event("INFO", "complex_extra", account_id="acc-7", extra=nested)

    rows = await list_recent_logs(limit=5)
    assert rows[0].extra == nested
    # And the on-disk JSON is canonical (sorted keys).
    raw = json.dumps(nested, default=str, sort_keys=True)
    assert raw == json.dumps(rows[0].extra, default=str, sort_keys=True)
