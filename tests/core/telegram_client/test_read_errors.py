"""Read dispatcher error and batching tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.stories import GetPinnedStoriesRequest
from telethon.tl.functions.users import GetFullUserRequest

from core.telegram_client import (
    TelegramAccountNotFoundError,
    TelegramReadError,
    execute_read,
    execute_read_many,
)
from core.telegram_client._pool import TelegramClientPoolError
from schemas.telegram_actions import (
    GetUserProfile,
    ListPinnedStories,
    ListProfileMusic,
)
from tests.core.telegram_client.helpers import patch_read_client as _patch_client


@pytest.mark.asyncio
async def test_execute_read_unknown_account_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(_account_id: str):
        return None

    monkeypatch.setattr("core.telegram_client._read.fetch_account", fake_fetch)

    with pytest.raises(TelegramAccountNotFoundError):
        await execute_read("ghost", GetUserProfile())


@pytest.mark.asyncio
async def test_execute_read_flood_wait_wraps_telethon_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            raise errors.FloodWaitError(request=None, capture=42)

    _patch_client(monkeypatch, FakeClient())

    with pytest.raises(TelegramReadError) as exc_info:
        await execute_read("acc-flood", GetUserProfile())

    assert exc_info.value.reason == "FloodWait(42s)"


@pytest.mark.asyncio
async def test_execute_read_rpc_error_wraps_telethon_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            raise errors.RPCError(request=None, message="USER_DEACTIVATED", code=400)

    _patch_client(monkeypatch, FakeClient())

    with pytest.raises(TelegramReadError) as exc_info:
        await execute_read("acc-rpc", GetUserProfile())

    assert "RPC" in exc_info.value.reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        TelegramClientPoolError("acc-pool", RuntimeError("connect failed")),
        ConnectionError("socket closed"),
        TimeoutError("handshake timed out"),
    ],
)
async def test_execute_read_wraps_infrastructure_errors(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
) -> None:
    """Infrastructure failures obey the read gateway error contract."""

    async def failing_get_client(_account_id: str) -> object:
        raise exc

    async def fake_fetch(account_id: str) -> object:
        return MagicMock(session_name=account_id)

    monkeypatch.setattr("core.telegram_client._read.get_client", failing_get_client)
    monkeypatch.setattr("core.telegram_client._read.fetch_account", fake_fetch)

    with pytest.raises(TelegramReadError) as exc_info:
        await execute_read("acc-pool", GetUserProfile())

    assert type(exc).__name__ in exc_info.value.reason
    assert exc_info.value.__cause__ is exc


@pytest.mark.asyncio
async def test_execute_read_many_opens_single_client_for_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a batch borrows the pool exactly ONCE for N actions.

    Originally the dialog opened 3 fresh Telethon clients in parallel and
    raced into ``OperationalError: database is locked``. Then ``execute_read_many``
    serialised into one per-call client. Now the pool keeps the client warm
    across batches, but the *single borrow per batch* invariant still
    holds — and it's tested at the seam (``get_client`` calls), not at the
    factory level which lives in the pool's own tests.
    """
    pool_borrows = 0
    handled: list[object] = []

    class FakeClient:
        async def get_input_entity(self, _name: str) -> object:
            return MagicMock()

        async def __call__(self, request: object) -> object:
            handled.append(request)
            if isinstance(request, GetFullUserRequest):
                return MagicMock(full_user=MagicMock(about=None), users=[MagicMock()])
            if isinstance(request, GetPinnedStoriesRequest):
                return MagicMock(stories=[])
            # GetSavedMusicRequest fallback
            return MagicMock(documents=[])

    shared_client = FakeClient()

    async def fake_get_client(_account_id: str) -> object:
        nonlocal pool_borrows
        pool_borrows += 1
        return shared_client

    async def fake_fetch(account_id: str):
        return MagicMock(session_name=account_id)

    monkeypatch.setattr("core.telegram_client._read.get_client", fake_get_client)
    monkeypatch.setattr("core.telegram_client._read.fetch_account", fake_fetch)

    results = await execute_read_many(
        "acc-batch",
        [GetUserProfile(), ListPinnedStories(), ListProfileMusic()],
    )

    assert pool_borrows == 1, "execute_read_many must borrow once per batch"
    assert len(results) == 3, "must return one result per action, in input order"
