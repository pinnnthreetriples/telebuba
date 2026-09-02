"""The live 2FA read carries a deadline, so the card can never spin forever.

Split from ``test_twofa.py`` for the 700-line test-file budget; same fixtures.
"""

from __future__ import annotations

import asyncio

import pytest

from core.config import settings
from core.db import create_account
from schemas.accounts import AccountCreate
from services.accounts import read_account_twofa


@pytest.mark.asyncio
async def test_read_reports_a_silent_gateway_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A half-open pooled client never answers; the envelope must still render."""
    await create_account(AccountCreate(account_id="acc-mute"))
    monkeypatch.setattr(settings.telegram, "session_check_timeout_seconds", 0.05)

    async def _mute(account_id: str, action: object) -> object:  # noqa: ARG001
        await asyncio.Event().wait()
        return None

    monkeypatch.setattr("services.accounts.twofa.execute_read", _mute)

    view = await read_account_twofa("acc-mute")

    assert view.status is None
    assert view.error == "unavailable: TimeoutError"
