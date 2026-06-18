"""Tests for ``core.telegram_client.check_spam_status`` — the @SpamBot probe."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

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

    monkeypatch.setattr("core.telegram_client._spam.telegram_client", fake_cm)


class _FakeTelethonClient:
    """Mock that mirrors the events.NewMessage + send_message contract.

    On ``send_message`` the fake invokes the handler that was registered via
    ``add_event_handler``, which models the real race-free flow: if the probe
    forgets to register the handler before sending, the bot reply is never
    delivered to the future and the probe times out.
    """

    def __init__(
        self,
        reply_text: str | None,
        *,
        restricted: bool = False,
        restriction_reason: list[object] | None = None,
    ) -> None:
        self._reply_text = reply_text
        self._restricted = restricted
        self._restriction_reason = restriction_reason or []
        self._handler: Any = None
        self.removed_handlers: list[object] = []

    async def connect(self) -> None:
        return None

    async def get_input_entity(self, username: str) -> str:
        return username

    def add_event_handler(self, handler: object, _event: object) -> None:
        self._handler = handler

    def remove_event_handler(self, handler: object) -> None:
        self.removed_handlers.append(handler)
        if self._handler is handler:
            self._handler = None

    async def send_message(self, _peer: object, _text: str) -> None:
        handler = self._handler
        if handler is not None:
            event = SimpleNamespace(raw_text=self._reply_text)
            await handler(event)

    async def get_me(self) -> object:
        return SimpleNamespace(
            restricted=self._restricted,
            restriction_reason=self._restriction_reason,
        )


@pytest.mark.asyncio
async def test_check_spam_status_reads_reply_and_restriction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeTelethonClient("Good news, no limits are applied.")
    _patch_client(monkeypatch, client)

    probe = await check_spam_status("acc-1")

    assert probe.error is None
    assert probe.reply_text is not None
    assert "no limits" in probe.reply_text.lower()
    assert probe.restricted is False


@pytest.mark.asyncio
async def test_check_spam_status_reports_restriction_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeTelethonClient(
        "hello",
        restricted=True,
        restriction_reason=[SimpleNamespace(text="spam", reason="")],
    )
    _patch_client(monkeypatch, client)

    probe = await check_spam_status("acc-1")

    assert probe.restricted is True
    assert probe.restriction_reason == "spam"


@pytest.mark.asyncio
async def test_check_spam_status_removes_event_handler_after_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe must clean up its handler so we don't leak listeners."""
    client = _FakeTelethonClient("Good news, no limits.")
    _patch_client(monkeypatch, client)

    await check_spam_status("acc-1")

    assert len(client.removed_handlers) == 1


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
