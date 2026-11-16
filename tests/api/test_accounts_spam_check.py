"""``POST /accounts/{id}/spam-check``: the operator's button, through the spam-intel gateway."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from schemas.accounts import AccountCreate
from schemas.spam_status import SpamStatusVerdict
from services.accounts import add_account
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.mark.asyncio
async def test_spam_check_returns_the_fresh_verdict(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str, *, confirm_out_of_turn: bool) -> SpamStatusVerdict:
        # The button cannot ask for confirmation yet, so the route confirms for it.
        assert confirm_out_of_turn is True
        return SpamStatusVerdict(
            account_id=account_id,
            status="limited",
            detail="until 2026-07-01",
            checked_at="2026-06-30T00:00:00+00:00",
            served_from="probe",
        )

    monkeypatch.setattr("services.spam_intel.operator_check", _fake)
    await add_account(AccountCreate(account_id="acc-1"))
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/spam-check")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "limited"
    assert body["detail"] == "until 2026-07-01"
    assert body["served_from"] == "probe"


@pytest.mark.asyncio
async def test_spam_check_says_when_the_budget_postponed_it(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refusal by the budget keeps the stored status and names the reason and the retry."""

    async def _fake(account_id: str, *, confirm_out_of_turn: bool) -> SpamStatusVerdict:  # noqa: ARG001
        return SpamStatusVerdict(
            account_id=account_id,
            status="clean",
            checked_at="2026-06-30T00:00:00+00:00",
            served_from="deferred",
            retry_at="2026-07-01T00:00:00+00:00",
            reason="proxy_spacing",
        )

    monkeypatch.setattr("services.spam_intel.operator_check", _fake)
    await add_account(AccountCreate(account_id="acc-1"))
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/spam-check")
    body = resp.json()
    assert (body["status"], body["served_from"], body["reason"]) == (
        "clean",
        "deferred",
        "proxy_spacing",
    )
    assert body["retry_at"] == "2026-07-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_spam_check_unknown_account_is_404(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing row is a 404 like every sibling route, not a 200 ``unknown``.

    ``refresh_spam_status`` deliberately degrades instead of raising (warming and
    neurocomment onboarding call it too), so the route does its own lookup — and
    the service must not be reached at all.
    """

    async def _fake(account_id: str, *, confirm_out_of_turn: bool) -> SpamStatusVerdict:  # noqa: ARG001
        msg = "service must not be reached for a missing account"
        raise AssertionError(msg)

    monkeypatch.setattr("services.spam_intel.operator_check", _fake)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-nope/spam-check")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
