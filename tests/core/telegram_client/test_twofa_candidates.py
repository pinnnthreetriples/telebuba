"""Signing in with every 2FA candidate: next on a wrong password, stop on anything else."""

from __future__ import annotations

import pytest
from telethon import errors

from core.db import (
    create_account,
    fetch_account_twofa_pair,
    set_account_twofa_password,
    set_account_twofa_pending,
)
from core.telegram_client._twofa_candidates import sign_in_with_passwords
from schemas.accounts import AccountCreate


class _Client:
    def __init__(self, good: str | None, *, fail: Exception | None = None) -> None:
        self.good = good
        self.fail = fail
        self.tried: list[str] = []

    async def sign_in(self, *, password: str) -> None:
        self.tried.append(password)
        if self.fail is not None:
            raise self.fail
        if password != self.good:
            raise errors.PasswordHashInvalidError(request=None)


async def _staged() -> None:
    await create_account(AccountCreate(account_id="a"))
    await set_account_twofa_password("a", "old")
    await set_account_twofa_pending("a", "new")


@pytest.mark.asyncio
async def test_a_pending_candidate_that_is_in_force_is_promoted() -> None:
    await _staged()
    client = _Client("new")

    accepted = await sign_in_with_passwords(client, "a", ["old", "new"])  # ty: ignore[invalid-argument-type]

    assert accepted == "new"
    assert client.tried == ["old", "new"]
    assert await fetch_account_twofa_pair("a") == ("new", None)


@pytest.mark.asyncio
async def test_the_current_password_stops_at_once_and_leaves_the_candidate() -> None:
    await _staged()
    client = _Client("old")

    await sign_in_with_passwords(client, "a", ["old", "new"])  # ty: ignore[invalid-argument-type]

    assert client.tried == ["old"]
    assert await fetch_account_twofa_pair("a") == ("old", "new")


@pytest.mark.asyncio
async def test_every_rejected_raises_the_last_rejection() -> None:
    await _staged()

    with pytest.raises(errors.PasswordHashInvalidError):
        await sign_in_with_passwords(_Client(None), "a", ["old", "new"])  # ty: ignore[invalid-argument-type]


@pytest.mark.asyncio
async def test_any_other_error_stops_without_trying_the_next() -> None:
    await _staged()
    client = _Client("new", fail=errors.FloodWaitError(request=None, capture=30))

    with pytest.raises(errors.FloodWaitError):
        await sign_in_with_passwords(client, "a", ["old", "new"])  # ty: ignore[invalid-argument-type]

    assert client.tried == ["old"]
