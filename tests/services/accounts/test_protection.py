"""The per-account protection summary: each check on and off, from stored facts only."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.db import (
    create_account,
    fetch_account,
    set_account_twofa_password,
    set_account_twofa_pending,
)
from core.repositories._accounts_passkey import save_account_passkey
from core.repositories.account_rekeys import save_rekey
from core.repositories.session_audits import save_session_audit
from schemas.accounts import AccountCreate, AccountRead
from schemas.passkey import StoredPasskey
from schemas.session_audit import AccountSessionAudit
from schemas.session_rekey import StoredRekey
from services.accounts import _protection as protection
from tests.factories import seed_account_proxy

_NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC).isoformat()


async def _account(account_id: str) -> AccountRead:
    await create_account(AccountCreate(account_id=account_id))
    account = await fetch_account(account_id)
    assert account is not None
    return account


async def _missing(account: AccountRead) -> list[str]:
    summary = (await protection.protection_for([account]))[account.account_id]
    assert summary.total == 7
    assert summary.score == 7 - len(summary.missing)
    return list(summary.missing)


def _vault(monkeypatch: pytest.MonkeyPatch, stems: set[str]) -> None:
    monkeypatch.setattr(protection, "vault_enabled", lambda: True)
    monkeypatch.setattr(
        protection,
        "list_versions",
        lambda stem: [Path(stem)] if stem in stems else [],
    )


@pytest.mark.asyncio
async def test_a_bare_account_has_every_protection_missing_in_order() -> None:
    account = await _account("bare")

    assert await _missing(account) == [
        "proxy",
        "session_copy",
        "own_key",
        "old_key_closed",
        "twofa_stored",
        "passkey",
        "audit_clean",
    ]


@pytest.mark.asyncio
async def test_proxy_check() -> None:
    await _account("px")
    await seed_account_proxy("px")
    account = await fetch_account("px")
    assert account is not None

    assert "proxy" not in await _missing(account)


@pytest.mark.asyncio
async def test_session_copy_follows_the_stored_session_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = (await _account("copy")).model_copy(update={"session_name": "stem-1"})
    _vault(monkeypatch, {"stem-1"})
    assert "session_copy" not in await _missing(account)

    _vault(monkeypatch, {"copy"})  # a copy of the account id, not of its session name
    assert "session_copy" in await _missing(account)


@pytest.mark.asyncio
async def test_a_disabled_vault_never_touches_the_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    accounts = [await _account("v1"), await _account("v2")]
    monkeypatch.setattr(protection, "vault_enabled", lambda: False)

    def boom(_stem: str) -> list[Path]:
        msg = "the disk was read"
        raise AssertionError(msg)

    monkeypatch.setattr(protection, "list_versions", boom)

    summaries = await protection.protection_for(accounts)

    assert all("session_copy" in s.missing for s in summaries.values())


@pytest.mark.asyncio
async def test_an_unreadable_vault_folder_counts_as_no_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = await _account("oserr")
    monkeypatch.setattr(protection, "vault_enabled", lambda: True)

    def broken(_stem: str) -> list[Path]:
        raise OSError

    monkeypatch.setattr(protection, "list_versions", broken)

    assert "session_copy" in await _missing(account)


def _rekey(account_id: str, **fields: str | None) -> StoredRekey:
    return StoredRekey.model_validate(
        {"account_id": account_id, "old_hash": "111", "rekeyed_at": _NOW, **fields}
    )


@pytest.mark.asyncio
async def test_a_closed_rekey_proves_an_own_key_and_a_closed_old_one() -> None:
    account = await _account("rk")
    await save_rekey(_rekey("rk", closed_at=_NOW))

    missing = await _missing(account)

    assert "own_key" not in missing
    assert "old_key_closed" not in missing


@pytest.mark.asyncio
async def test_an_open_rekey_is_an_own_key_with_the_old_one_still_open() -> None:
    account = await _account("open")
    await save_rekey(_rekey("open", close_after=_NOW))

    missing = await _missing(account)

    assert "own_key" not in missing
    assert "old_key_closed" in missing


@pytest.mark.asyncio
async def test_a_rekey_with_a_problem_is_not_closed() -> None:
    account = await _account("prob")
    await save_rekey(_rekey("prob", closed_at=_NOW, problem="old_key_unknown"))

    missing = await _missing(account)

    assert "own_key" not in missing
    assert "old_key_closed" in missing


@pytest.mark.asyncio
async def test_only_a_verified_cloud_password_counts() -> None:
    pending_only = await _account("pend")
    await set_account_twofa_pending("pend", "seller-guess")
    verified = await _account("ver")
    await set_account_twofa_password("ver", "right")

    assert "twofa_stored" in await _missing(pending_only)
    assert "twofa_stored" not in await _missing(verified)


def _passkey(account_id: str) -> StoredPasskey:
    return StoredPasskey(
        account_id=account_id,
        credential_id="Y3JlZA",
        user_handle="1:2",
        vault_key_name=f"tb-{account_id}",
        created_at=_NOW,
    )


@pytest.mark.asyncio
async def test_passkey_check() -> None:
    account = await _account("pk")
    await save_account_passkey(_passkey("pk"))

    assert "passkey" not in await _missing(account)


async def _audit(account_id: str, foreign: int) -> None:
    await save_session_audit(
        AccountSessionAudit(
            account_id=account_id,
            checked_at=_NOW,
            total=foreign,
            foreign_count=foreign,
            sessions=[],
        )
    )


@pytest.mark.asyncio
async def test_never_audited_is_missing_not_clean() -> None:
    never = await _account("never")
    clean = await _account("clean")
    dirty = await _account("dirty")
    await _audit("clean", 0)
    await _audit("dirty", 2)

    assert "audit_clean" in await _missing(never)
    assert "audit_clean" not in await _missing(clean)
    assert "audit_clean" in await _missing(dirty)


@pytest.mark.asyncio
async def test_a_fully_protected_account_scores_seven(monkeypatch: pytest.MonkeyPatch) -> None:
    await _account("full")
    await seed_account_proxy("full")
    account = await fetch_account("full")
    assert account is not None
    _vault(monkeypatch, {"full"})
    await save_rekey(_rekey("full", closed_at=_NOW))
    await set_account_twofa_password("full", "pw")
    await save_account_passkey(_passkey("full"))
    await _audit("full", 0)

    summary = (await protection.protection_for([account]))["full"]

    assert (summary.score, summary.total, summary.missing) == (7, 7, [])


@pytest.mark.asyncio
async def test_the_batch_reads_each_source_once_however_many_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts = [await _account(f"b{i}") for i in range(5)]
    calls: dict[str, int] = {}

    for name in (
        "list_rekeys",
        "list_verified_twofa_account_ids",
        "list_passkey_account_ids",
        "list_foreign_session_counts",
    ):
        original = getattr(protection, name)

        def make(name: str = name, original=original):
            async def wrapper() -> object:
                calls[name] = calls.get(name, 0) + 1
                return await original()

            return wrapper

        monkeypatch.setattr(protection, name, make())

    await protection.protection_for(accounts)

    assert calls == {
        "list_rekeys": 1,
        "list_verified_twofa_account_ids": 1,
        "list_passkey_account_ids": 1,
        "list_foreign_session_counts": 1,
    }


@pytest.mark.asyncio
async def test_no_accounts_reads_nothing() -> None:
    assert await protection.protection_for([]) == {}
