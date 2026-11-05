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


class _StubbornClient:
    """The disconnect swallows its first cancellation and keeps running."""

    def __init__(self) -> None:
        self.session = _Session()
        self.task: asyncio.Task[None] | None = None
        self.running_at_close = False

    async def _disconnect(self) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(10)  # carries on past the first cancellation, ends on the next

    def disconnect(self) -> asyncio.Task[None]:
        self.task = asyncio.ensure_future(self._disconnect())
        return self.task


@pytest.mark.asyncio
async def test_a_disconnect_that_ignores_cancellation_cannot_hold_the_caller_or_the_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wait is bounded even when the inner task never ends, and the handle is closed."""
    monkeypatch.setattr(_pool, "_DISCONNECT_TIMEOUT_S", 0.01)
    monkeypatch.setattr(_pool, "_CANCEL_GRACE_S", 0.01)
    client = _StubbornClient()

    await asyncio.wait_for(_pool._safe_disconnect(client), 2)  # ty: ignore[invalid-argument-type]

    assert client.session.closed
    assert client.task is not None
    client.task.cancel()  # tidy: the loop would otherwise warn about a pending task
