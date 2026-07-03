"""Tests for the dialogue feed read model (repo + service).

Covers ``core.repositories.dialogues.list_recent_dialogue_messages`` and
``services.dialogues.load_dialogue_overview`` — the labeled, newest-first
conversation feed the SPA renders.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    configure_database,
    create_account,
    list_recent_dialogue_messages,
    record_dialogue_message,
    update_account_from_session_check,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate
from schemas.telegram_session import TelegramSessionCheckResult
from services.dialogues import load_dialogue_overview

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


async def _seed_account(account_id: str, phone: str) -> None:
    await create_account(AccountCreate(account_id=account_id, label=f"lbl-{account_id}"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id=account_id,
            session_path=account_id,
            status="alive",
            is_temporary=False,
            phone=phone,
        ),
    )


@pytest.mark.asyncio
async def test_list_recent_dialogue_messages_newest_first() -> None:
    await create_account(AccountCreate(account_id="a"))
    await create_account(AccountCreate(account_id="b"))
    await record_dialogue_message("a", "b", "first")
    await record_dialogue_message("b", "a", "second")

    recent = await list_recent_dialogue_messages()

    assert [msg.text for msg in recent] == ["second", "first"]


@pytest.mark.asyncio
async def test_list_recent_dialogue_messages_respects_limit() -> None:
    await create_account(AccountCreate(account_id="a"))
    await create_account(AccountCreate(account_id="b"))
    for i in range(5):
        await record_dialogue_message("a", "b", f"m{i}")

    recent = await list_recent_dialogue_messages(limit=2)

    assert [msg.text for msg in recent] == ["m4", "m3"]


@pytest.mark.asyncio
async def test_load_overview_resolves_phone_labels_newest_first() -> None:
    await _seed_account("a", "+15550001111")
    await _seed_account("b", "+15550002222")
    await record_dialogue_message("a", "b", "hello")
    await record_dialogue_message("b", "a", "hi back")

    feed = await load_dialogue_overview()

    assert [msg.text for msg in feed.messages] == ["hi back", "hello"]
    newest = feed.messages[0]
    assert newest.from_account == "b"
    assert newest.from_label == "+15550002222"
    assert newest.to_account == "a"
    assert newest.to_label == "+15550001111"


@pytest.mark.asyncio
async def test_load_overview_empty_state() -> None:
    feed = await load_dialogue_overview()
    assert feed.messages == []


@pytest.mark.asyncio
async def test_load_overview_falls_back_to_label_then_id() -> None:
    # Account with a label but no phone → falls back to label.
    await create_account(AccountCreate(account_id="a", label="Alfa"))
    # Peer account never registered → falls back to the bare id.
    await record_dialogue_message("a", "ghost", "orphan")

    feed = await load_dialogue_overview()

    msg = feed.messages[0]
    assert msg.from_label == "Alfa"
    assert msg.to_label == "ghost"


@pytest.mark.asyncio
async def test_load_overview_respects_limit() -> None:
    await create_account(AccountCreate(account_id="a"))
    await create_account(AccountCreate(account_id="b"))
    for i in range(4):
        await record_dialogue_message("a", "b", f"m{i}")

    feed = await load_dialogue_overview(recent_limit=1)

    assert [msg.text for msg in feed.messages] == ["m3"]
