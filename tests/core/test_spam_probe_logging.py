"""The @SpamBot probe and the history read log the exception CLASS, never its text.

An exception raised by a pooled client or by Telethon can carry a proxy endpoint, a session path
or a fragment of a message. The local debug log is not a place for any of it, so the probe paths
log the class name only (and no traceback, which prints the message again).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import check_spam_status, converse_spam_status, read_spambot_history
from schemas.spam_probe import HumanProbePlan

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

SECRET = "proxy 203.0.113.9:1080 user:hunter2 refused /srv/sessions/acc-1.session"
PLAN = HumanProbePlan(
    pre_open=0,
    open_to_typing=0,
    typing_seconds=0,
    reply_wait_max=1,
    quiet_after_last_reply=1,
    read_delay=0,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


@pytest.fixture(autouse=True)
def _failing_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_get_client(_account_id: str, _device_type: str | None = None) -> Any:
        raise ConnectionError(SECRET)

    monkeypatch.setattr("core.telegram_client._spam.get_client", failing_get_client)


@pytest.mark.asyncio
async def test_a_failed_probe_logs_the_class_not_the_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        single = await check_spam_status("acc-1")
        human = await converse_spam_status("acc-1", PLAN)
    assert (single.error, human.error) == ("ConnectionError", "ConnectionError")
    assert "ConnectionError" in caplog.text
    for leaked in ("203.0.113.9", "hunter2", "acc-1.session", "refused"):
        assert leaked not in caplog.text


@pytest.mark.asyncio
async def test_a_failed_history_read_logs_the_class_not_the_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        history = await read_spambot_history("acc-1", 5)
    assert history.error == "ConnectionError"
    assert "ConnectionError" in caplog.text
    for leaked in ("203.0.113.9", "hunter2", "acc-1.session", "refused"):
        assert leaked not in caplog.text
