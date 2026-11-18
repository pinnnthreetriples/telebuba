"""The observation and sweep paths log an exception's class, never its text (audit F-027)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from services import spam_intel, spam_status
from services.spam_intel import _history

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    MakeAccount = Callable[..., Awaitable[None]]

SECRET = "token-hunter2 at /srv/sessions/acc-1.session behind 203.0.113.9"


@pytest.mark.asyncio
async def test_a_sample_that_cannot_be_stored_logs_the_class_only(
    make_account: MakeAccount,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(SECRET)

    monkeypatch.setattr(spam_status, "upsert_reply_sample", _boom)
    await make_account("acc-1")
    with caplog.at_level(logging.DEBUG):
        await spam_intel.ask("acc-1", force=True)
    assert "RuntimeError" in caplog.text
    for leaked in ("hunter2", "acc-1.session", "203.0.113.9"):
        assert leaked not in caplog.text


@pytest.mark.asyncio
async def test_a_crashing_sweep_logs_the_class_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def _boom() -> int:
        raise RuntimeError(SECRET)

    monkeypatch.setattr(_history, "run_history_sweep", _boom)
    with caplog.at_level(logging.DEBUG):
        await _history._run()
    assert "RuntimeError" in caplog.text
    for leaked in ("hunter2", "acc-1.session", "203.0.113.9"):
        assert leaked not in caplog.text
