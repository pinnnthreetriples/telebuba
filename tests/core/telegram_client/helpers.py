from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    import pytest


def patch_action_client(
    monkeypatch: pytest.MonkeyPatch,
    client: object,
) -> None:
    async def fake_get_client(_account_id: str) -> object:
        return client

    async def fake_fetch(account_id: str) -> MagicMock:
        return MagicMock(session_name=account_id)

    monkeypatch.setattr("core.telegram_client._actions.get_client", fake_get_client)
    monkeypatch.setattr("core.telegram_client._actions.fetch_account", fake_fetch)


def patch_read_client(
    monkeypatch: pytest.MonkeyPatch,
    client: object,
) -> None:
    async def fake_get_client(_account_id: str) -> object:
        return client

    async def fake_fetch(account_id: str) -> MagicMock:
        return MagicMock(session_name=account_id)

    monkeypatch.setattr("core.telegram_client._read.get_client", fake_get_client)
    monkeypatch.setattr("core.telegram_client._read.fetch_account", fake_fetch)
