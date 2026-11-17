"""``POST /accounts/{id}/spam-check``: the operator's button, through the spam-intel gateway.

T14 of docs/spam-intel.md: the additive result (``probed``, ``next_allowed_at``), the
``confirm_out_of_turn`` request field and the 409 an unconfirmed out-of-turn click gets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from schemas.accounts import AccountCreate
from schemas.spam_status import SpamStatusVerdict
from services.accounts import add_account
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI

_PATCH = "services.spam_intel._card.operator_check"


def _verdict(**fields: object) -> SpamStatusVerdict:
    base: dict[str, object] = {
        "account_id": "acc-1",
        "status": "clean",
        "checked_at": "2026-06-30T00:00:00+00:00",
    }
    return SpamStatusVerdict.model_validate({**base, **fields})


@pytest.mark.asyncio
async def test_spam_check_returns_the_fresh_verdict(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[bool] = []

    async def _fake(account_id: str, *, confirm_out_of_turn: bool) -> SpamStatusVerdict:
        seen.append(confirm_out_of_turn)
        return _verdict(
            account_id=account_id,
            status="limited",
            detail="until 2026-07-01",
            served_from="probe",
        )

    monkeypatch.setattr(_PATCH, _fake)
    await add_account(AccountCreate(account_id="acc-1"))
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/spam-check")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "limited"
    assert body["detail"] == "until 2026-07-01"
    assert body["served_from"] == "probe"
    # A real question went out; the coalescing window after it is when the next may.
    assert body["probed"] is True
    assert body["next_allowed_at"] is None  # no ledger row in this stubbed run
    # No body, no confirmation: the click is unconfirmed unless the operator says so.
    assert seen == [False]


@pytest.mark.asyncio
async def test_spam_check_passes_the_confirmation_through(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[bool] = []

    async def _fake(account_id: str, *, confirm_out_of_turn: bool) -> SpamStatusVerdict:
        seen.append(confirm_out_of_turn)
        return _verdict(account_id=account_id, served_from="probe")

    monkeypatch.setattr(_PATCH, _fake)
    await add_account(AccountCreate(account_id="acc-1"))
    async with _client(app) as client:
        await client.post("/api/v1/accounts/acc-1/spam-check", json={"confirm_out_of_turn": True})
        await client.post("/api/v1/accounts/acc-1/spam-check", json={})
        # What the generated client sends when no body was given: JSON type, empty content.
        await client.post(
            "/api/v1/accounts/acc-1/spam-check",
            content=b"",
            headers={"Content-Type": "application/json"},
        )
    assert seen == [True, False, False]


@pytest.mark.asyncio
async def test_an_unconfirmed_out_of_turn_click_is_a_409_with_a_clear_code(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str, *, confirm_out_of_turn: bool) -> SpamStatusVerdict:
        if confirm_out_of_turn:
            return _verdict(account_id=account_id, served_from="probe")
        return _verdict(account_id=account_id, served_from="refused", reason="needs_confirmation")

    monkeypatch.setattr(_PATCH, _fake)
    await add_account(AccountCreate(account_id="acc-1"))
    async with _client(app) as client:
        refused = await client.post("/api/v1/accounts/acc-1/spam-check")
        confirmed = await client.post(
            "/api/v1/accounts/acc-1/spam-check", json={"confirm_out_of_turn": True}
        )
    assert refused.status_code == 409
    error = refused.json()["error"]
    assert (error["code"], error["message"]) == ("conflict", "needs_confirmation")
    assert confirmed.status_code == 200
    assert confirmed.json()["probed"] is True


@pytest.mark.asyncio
async def test_spam_check_says_when_the_budget_postponed_it(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refusal by the budget keeps the stored status and names the reason and the retry."""

    async def _fake(account_id: str, *, confirm_out_of_turn: bool) -> SpamStatusVerdict:  # noqa: ARG001
        return _verdict(
            account_id=account_id,
            served_from="deferred",
            retry_at="2026-07-01T00:00:00+00:00",
            reason="proxy_spacing",
        )

    monkeypatch.setattr(_PATCH, _fake)
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
    # Nothing was asked; the hold's own retry time is when it may be.
    assert body["probed"] is False
    assert body["next_allowed_at"] == "2026-07-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_other_refusals_stay_a_200_with_their_reason(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str, *, confirm_out_of_turn: bool) -> SpamStatusVerdict:  # noqa: ARG001
        return _verdict(account_id=account_id, served_from="refused", reason="operator_daily_cap")

    monkeypatch.setattr(_PATCH, _fake)
    await add_account(AccountCreate(account_id="acc-1"))
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/spam-check")
    assert resp.status_code == 200
    assert (resp.json()["served_from"], resp.json()["reason"]) == ("refused", "operator_daily_cap")


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

    monkeypatch.setattr(_PATCH, _fake)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-nope/spam-check")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
