"""A standing subscription moves with the account's primary device — it is never doubled.

Adding a desktop to a mobile-only account makes desktop the primary for new borrows,
while the mobile client stays connected for whoever names it. The pool's rebuild hook
then puts the post / inbox handler on the desktop client; it must come off the mobile
one, or every update would be delivered twice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import _listener, _message_listener

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    _listener._reset_for_tests()
    _message_listener._reset_for_tests()
    yield
    _listener._reset_for_tests()
    _message_listener._reset_for_tests()
    reset_logging_for_tests()


class _Client:
    def __init__(self, name: str) -> None:
        self.name = name
        self.handlers: list[tuple[Any, object]] = []

    async def get_peer_id(self, _channel: str) -> int:
        return -100

    async def is_user_authorized(self) -> bool:
        return True

    async def catch_up(self) -> None:
        return None

    def add_event_handler(self, handler: Any, event_filter: object) -> None:
        self.handlers.append((handler, event_filter))

    def remove_event_handler(self, handler: Any, _event_filter: object) -> int:
        self.handlers = [item for item in self.handlers if item[0] is not handler]
        return 1


async def _noop(_event: object) -> None:
    return None


@pytest.mark.asyncio
async def test_the_post_handler_leaves_the_old_primary_when_it_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mobile, desktop = _Client("mobile"), _Client("desktop")

    async def borrow(_account_id: str, _device_type: str | None = None) -> _Client:
        return mobile

    monkeypatch.setattr(_listener, "get_client", borrow)
    await _listener.subscribe_posts("acc", ["@news"], _noop)

    await _listener._reattach_on_rebuild("acc", desktop)  # ty: ignore[invalid-argument-type]

    assert mobile.handlers == []
    assert len(desktop.handlers) == 1


@pytest.mark.asyncio
async def test_the_inbox_handler_leaves_the_old_primary_when_it_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mobile, desktop = _Client("mobile"), _Client("desktop")

    async def borrow(_account_id: str, _device_type: str | None = None) -> _Client:
        return mobile

    monkeypatch.setattr(_message_listener, "get_client", borrow)
    assert await _message_listener.subscribe_incoming_messages("acc", _noop)

    await _message_listener._reattach_on_rebuild("acc", desktop)  # ty: ignore[invalid-argument-type]

    assert mobile.handlers == []
    assert len(desktop.handlers) == 1


@pytest.mark.asyncio
async def test_a_rebuild_of_the_same_client_keeps_one_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client("desktop")

    async def borrow(_account_id: str, _device_type: str | None = None) -> _Client:
        return client

    monkeypatch.setattr(_message_listener, "get_client", borrow)
    assert await _message_listener.subscribe_incoming_messages("acc", _noop)

    assert await _message_listener.ensure_incoming_message_connection("acc")

    assert len(client.handlers) == 1
