"""Tests for ``core.telegram_client.check_spam_status`` — the @SpamBot probe."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from core import telegram_client as telegram_client_module
from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import check_spam_status

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


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


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    @asynccontextmanager
    async def fake_cm(_request: object):
        yield client

    monkeypatch.setattr(telegram_client_module, "telegram_client", fake_cm)


class _FakeConversation:
    def __init__(self, reply_text: str) -> None:
        self._reply_text = reply_text

    async def send_message(self, _text: str) -> None:
        return None

    async def get_response(self) -> object:
        return SimpleNamespace(text=self._reply_text)


@pytest.mark.asyncio
async def test_check_spam_status_reads_reply_and_restriction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        def conversation(self, _username: str, *, timeout: float) -> object:  # noqa: ARG002
            @asynccontextmanager
            async def cm():
                yield _FakeConversation("Good news, no limits are applied.")

            return cm()

        async def get_me(self) -> object:
            return SimpleNamespace(restricted=False, restriction_reason=[])

    _patch_client(monkeypatch, FakeClient())

    probe = await check_spam_status("acc-1")

    assert probe.error is None
    assert probe.reply_text is not None
    assert "no limits" in probe.reply_text.lower()
    assert probe.restricted is False


@pytest.mark.asyncio
async def test_check_spam_status_reports_restriction_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        def conversation(self, _username: str, *, timeout: float) -> object:  # noqa: ARG002
            @asynccontextmanager
            async def cm():
                yield _FakeConversation("hello")

            return cm()

        async def get_me(self) -> object:
            return SimpleNamespace(
                restricted=True,
                restriction_reason=[SimpleNamespace(text="spam", reason="")],
            )

    _patch_client(monkeypatch, FakeClient())

    probe = await check_spam_status("acc-1")

    assert probe.restricted is True
    assert probe.restriction_reason == "spam"


@pytest.mark.asyncio
async def test_check_spam_status_classifies_failure_as_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            msg = "boom"
            raise RuntimeError(msg)

    _patch_client(monkeypatch, FakeClient())

    probe = await check_spam_status("acc-1")

    assert probe.reply_text is None
    assert probe.error is not None
    assert "RuntimeError" in probe.error
