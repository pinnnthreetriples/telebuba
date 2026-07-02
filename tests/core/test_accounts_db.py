from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from core.db import (
    configure_database,
    create_account,
    get_listener_account_id,
    insert_challenge,
    insert_device_fingerprint,
    list_accounts,
    list_challenged_channels,
    set_listener_account_id,
    update_account_from_session_check,
)
from core.repositories.accounts import _delete_account
from schemas.accounts import AccountCreate
from schemas.challenge import ChallengeInsert
from schemas.telegram_session import TelegramSessionCheckResult
from tests.factories import DeviceFingerprintFactory

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_create_account_lists_device_profile(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")
    await create_account(
        AccountCreate(account_id="account-1", label="Main", session_name="session-1"),
    )
    await insert_device_fingerprint(
        DeviceFingerprintFactory.build(account_id="account-1"),
    )

    accounts = await list_accounts()

    assert len(accounts.accounts) == 1
    account = accounts.accounts[0]
    assert account.account_id == "account-1"
    assert account.label == "Main"
    assert account.session_name == "session-1"
    assert account.status == "new"
    assert account.device_model == "Desktop"


@pytest.mark.parametrize(
    "bad_id",
    [
        "with|pipe",
        "with space",
        "with/slash",
        'has"quote',
        "",
    ],
)
def test_account_id_rejects_unsafe_charset(bad_id: str) -> None:
    """pair_key joins account_ids with '|' — refuse anything that breaks the contract."""
    with pytest.raises(ValidationError):
        AccountCreate(account_id=bad_id)


@pytest.mark.asyncio
async def test_create_duplicate_account_returns_existing_row(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")

    first = await create_account(AccountCreate(account_id="same", label="First"))
    second = await create_account(AccountCreate(account_id="same", label="Second"))

    assert first == second
    assert second.label == "First"


@pytest.mark.asyncio
async def test_update_account_from_alive_session_check(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreate(account_id="account-2"))

    updated = await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="account-2",
            session_path="sessions/account-2",
            status="alive",
            is_temporary=False,
            user_id=123,
            phone="100200300",
            username="username",
            first_name="First",
            last_name="Last",
        ),
    )

    assert updated.status == "alive"
    assert updated.user_id == 123
    assert updated.username == "username"
    assert updated.last_checked_at is not None


@pytest.mark.asyncio
async def test_update_account_from_temporary_session_check_keeps_identity(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreate(account_id="account-3"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="account-3",
            session_path="sessions/account-3",
            status="alive",
            is_temporary=False,
            user_id=456,
            username="saved",
        ),
    )

    updated = await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="account-3",
            session_path="sessions/account-3",
            status="network_error",
            is_temporary=True,
            error_type="TimeoutError",
            error_message="timeout",
        ),
    )

    assert updated.status == "network_error"
    assert updated.user_id == 456
    assert updated.username == "saved"


@pytest.mark.asyncio
async def test_delete_account_purges_neurocomment_challenges(tmp_path: Path) -> None:
    """Audit #1: challenge rows must not outlive the account (channel stays 'challenged')."""
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreate(account_id="acc-x"))
    await insert_challenge(
        ChallengeInsert(
            challenge_hash="h1",
            account_id="acc-x",
            channel="@chan",
            raw_text="solve me",
            button_labels=["A", "B"],
            outcome="give_up",
        ),
    )
    # Before delete the channel reads as challenged (a give_up row exists).
    assert (await list_challenged_channels(["@chan"])).channels == ["@chan"]

    await asyncio.to_thread(_delete_account, "acc-x")

    # After delete no challenge row remains, so the board no longer flags it.
    assert (await list_challenged_channels(["@chan"])).channels == []


@pytest.mark.asyncio
async def test_delete_account_clears_listener_pointer_when_it_was_listener(
    tmp_path: Path,
) -> None:
    """Audit #1: deleting the listener account clears the persisted pointer."""
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreate(account_id="acc-listener"))
    await set_listener_account_id("acc-listener")

    await asyncio.to_thread(_delete_account, "acc-listener")

    assert await get_listener_account_id() is None


@pytest.mark.asyncio
async def test_delete_account_leaves_other_listener_pointer_intact(tmp_path: Path) -> None:
    """Deleting a non-listener account must not disturb the pointer."""
    configure_database(tmp_path / "telebuba.db")
    await create_account(AccountCreate(account_id="acc-listener"))
    await create_account(AccountCreate(account_id="acc-other"))
    await set_listener_account_id("acc-listener")

    await asyncio.to_thread(_delete_account, "acc-other")

    assert await get_listener_account_id() == "acc-listener"
