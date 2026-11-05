"""``_safe_disconnect`` must not leave the SQLite session open when the wait times out."""

from __future__ import annotations

import asyncio

import pytest

from core.telegram_client import _pool


class _Session:
    closed = False

    def close(self) -> None:
        self.closed = True


class _HungClient:
    def __init__(self) -> None:
        self.session = _Session()

    def disconnect(self) -> asyncio.Future[None]:
        return asyncio.get_running_loop().create_future()


@pytest.mark.asyncio
async def test_safe_disconnect_timeout_closes_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_pool, "_DISCONNECT_TIMEOUT_S", 0.01)
    client = _HungClient()
    await _pool._safe_disconnect(client)  # ty: ignore[invalid-argument-type]
    assert client.session.closed
