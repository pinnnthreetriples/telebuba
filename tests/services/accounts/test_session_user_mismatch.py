"""A session check never rewrites an account into another Telegram user."""

from __future__ import annotations

import pytest

from core.db import fetch_account
from schemas.accounts import AccountCheckRequest, AccountCreate
from schemas.telegram_session import TelegramSessionCheckResult
from services.accounts import AccountActionError, add_account, check_account_session


def _alive(user_id: int, username: str) -> TelegramSessionCheckResult:
    return TelegramSessionCheckResult(
        account_id="acc",
        session_path="sessions/acc",
        status="alive",
        is_temporary=False,
        user_id=user_id,
        username=username,
    )


@pytest.mark.asyncio
async def test_a_key_of_another_user_is_refused_and_the_row_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await add_account(AccountCreate(account_id="acc"))
    answers = [_alive(111, "owner"), _alive(222, "stranger"), _alive(111, "owner")]

    async def _check(_request: object) -> TelegramSessionCheckResult:
        return answers.pop(0)

    monkeypatch.setattr("services.accounts.sessions.check_telegram_session", _check)
    await check_account_session(AccountCheckRequest(account_id="acc"))

    with pytest.raises(AccountActionError) as refused:
        await check_account_session(AccountCheckRequest(account_id="acc"))

    assert refused.value.code == "session_user_mismatch"
    row = await fetch_account("acc")
    assert row is not None
    assert (row.user_id, row.username) == (111, "owner")
    # The same user again is an ordinary check.
    assert (await check_account_session(AccountCheckRequest(account_id="acc"))).user_id == 111


@pytest.mark.asyncio
async def test_an_account_that_never_had_a_user_takes_the_first_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await add_account(AccountCreate(account_id="acc"))

    async def _check(_request: object) -> TelegramSessionCheckResult:
        return _alive(333, "first")

    monkeypatch.setattr("services.accounts.sessions.check_telegram_session", _check)

    assert (await check_account_session(AccountCheckRequest(account_id="acc"))).user_id == 333
