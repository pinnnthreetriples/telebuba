"""A link-failed (alive + link_status) account must be deferred by the gateway's budget too."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from schemas.accounts import AccountRead
from services.spam_intel import _gateway
from services.spam_intel._answers import Ask

if TYPE_CHECKING:
    from schemas.spam_intel import BudgetSnapshot


@pytest.mark.asyncio
async def test_decide_at_marks_a_link_failed_account_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[object] = []

    async def _snapshot(*_args: object) -> object:
        return object()

    def _capture(_snapshot: BudgetSnapshot, request: object, _cfg: object) -> object:
        seen.append(request)
        return object()

    monkeypatch.setattr(_gateway, "load_budget_snapshot", _snapshot)
    monkeypatch.setattr(_gateway, "_decide", _capture)
    account = AccountRead(
        account_id="a",
        status="alive",
        link_status="proxy_error",
        proxy_id="p",
        created_at="t",
        updated_at="t",
    )
    ask = Ask("a", False, "routine", "test", None, False)  # noqa: FBT003
    await _gateway._decide_at(account, ask, datetime.now(UTC))
    assert seen[0].transient is True  # ty: ignore[unresolved-attribute]
