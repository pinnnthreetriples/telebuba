"""Reactions-availability threading through the channel service.

Its own file, not more cases in ``test_accounts_channels.py``: that one sits at
the repo's 700-line cap for test sources, and the three assertions here are the
ones nothing else can make. Each pins one crossing of the service boundary that
a dropped keyword argument would silently break — the operator's choice would
be ignored on create, the editor's toggle would no-op (or 400, since an
``EditChannel`` with every field ``None`` fails its validator), and a channel
whose reactions are off would render as on, leaving the toggle unable to change
anything.

Same patch seams as its sibling: ``execute`` / ``execute_read`` on the owning
``services.accounts.channels`` module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from schemas.channels import ChannelCreateRequest, ChannelUpdateRequest
from schemas.telegram_actions import ActionResult, CreateChannel, EditChannel
from schemas.telegram_actions_channels import TelegramOwnChannelDetail
from services.accounts import (
    create_account_channel,
    get_account_channel,
    update_account_channel,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from pydantic import BaseModel


@pytest.fixture(autouse=True)
def _isolate_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


def _patch_execute(monkeypatch: pytest.MonkeyPatch, action_type: str) -> list[object]:
    captured: list[object] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type=action_type, account_id=account_id)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr("services.accounts.channels.execute", fake_execute)
    return captured


def _patch_read(monkeypatch: pytest.MonkeyPatch, result: BaseModel) -> None:
    async def fake_execute_read(_account_id: str, _action: object) -> BaseModel:
        return result

    monkeypatch.setattr("services.accounts.channels.execute_read", fake_execute_read)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.asyncio
async def test_create_account_channel_threads_reactions_choice(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool,
) -> None:
    captured = _patch_execute(monkeypatch, "channel_create")

    await create_account_channel(
        "acc-1",
        ChannelCreateRequest(title="Silent", reactions_enabled=enabled),
    )

    action = captured[0]
    assert isinstance(action, CreateChannel)
    assert action.reactions_enabled is enabled


@pytest.mark.asyncio
async def test_update_account_channel_threads_a_reactions_only_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reactions alone must reach the action — and satisfy its validator."""
    captured = _patch_execute(monkeypatch, "channel_edit")

    await update_account_channel("acc-1", 42, ChannelUpdateRequest(reactions_enabled=False))

    action = captured[0]
    assert isinstance(action, EditChannel)
    assert action.reactions_enabled is False
    assert action.title is None
    assert action.about is None


@pytest.mark.asyncio
async def test_get_account_channel_maps_reactions_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``False`` on purpose, because both models default to ``True``.

    A mapping that dropped the field would otherwise pass an assertion made
    against that shared default.
    """
    _patch_read(
        monkeypatch,
        TelegramOwnChannelDetail(
            channel_id=42,
            title="Silent",
            about="",
            reactions_enabled=False,
        ),
    )

    view = await get_account_channel("acc-1", 42)

    assert view.reactions_enabled is False
