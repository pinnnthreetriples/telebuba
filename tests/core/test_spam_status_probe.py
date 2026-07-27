"""Tests for ``core.telegram_client.check_spam_status`` — the @SpamBot probe."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import check_spam_status
from core.telegram_client._pool import TelegramClientPoolError

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
    """Stand in for the pool.

    The probe borrows the account's pooled client so it never opens a rival
    connection to an already-open ``.session`` file.
    """

    async def fake_get_client(_account_id: str) -> object:
        return client

    monkeypatch.setattr("core.telegram_client._spam.get_client", fake_get_client)


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
        async def get_input_entity(self, _username: str) -> str:
            msg = "boom"
            raise RuntimeError(msg)

    _patch_client(monkeypatch, FakeClient())

    probe = await check_spam_status("acc-1")

    assert probe.reply_text is None
    assert probe.error is not None
    assert "RuntimeError" in probe.error


@pytest.mark.asyncio
async def test_check_spam_status_reports_a_pool_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pool that can't connect must classify, not escape — the probe never raises."""

    async def failing_get_client(_account_id: str) -> object:
        msg = "no route"
        raise ConnectionError(msg)

    monkeypatch.setattr("core.telegram_client._spam.get_client", failing_get_client)

    probe = await check_spam_status("acc-1")

    assert probe.error is not None
    assert "ConnectionError" in probe.error


@pytest.mark.asyncio
async def test_probe_error_is_the_class_name_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """``probe.error`` becomes ``SpamStatusVerdict.detail`` — a response body field.

    ``services.spam_status.classify_spam_probe`` copies it verbatim into ``detail``,
    which is the response model of ``POST /accounts/{id}/spam-check`` and is
    re-served as ``AccountRead.spam_detail``, rendered in the operator's browser.
    ``f"{type(exc).__name__}: {exc}"`` therefore published the proxy endpoint from a
    pooled-client failure (and a ``.session`` path from a session fault).
    """
    endpoint = "203.0.113.9:1080"

    async def failing_get_client(account_id: str) -> object:
        raise TelegramClientPoolError(
            account_id,
            OSError(f"Could not connect to proxy {endpoint} [Connection refused]"),
        )

    monkeypatch.setattr("core.telegram_client._spam.get_client", failing_get_client)

    probe = await check_spam_status("acc-1")

    assert probe.error == "TelegramClientPoolError"
    assert endpoint not in (probe.error or "")
