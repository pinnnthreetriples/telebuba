"""The Accounts page's list filters: status buckets, proxy country, phone code, Trust."""

from __future__ import annotations

import pytest

from core.db import update_account_from_session_check, update_proxy_check, upsert_spam_status
from schemas.accounts import AccountCreate, AccountPage, AccountStatus
from schemas.proxy import ProxyCheckUpdate
from schemas.spam_status import SpamStatusVerdict
from schemas.telegram_session import TelegramSessionCheckResult
from services.accounts import account_filter_options, add_account, list_accounts_page
from tests.factories import seed_account_proxy


async def _account(
    account_id: str, status: AccountStatus = "new", phone: str | None = None
) -> None:
    await add_account(AccountCreate(account_id=account_id, phone=phone))
    if status != "new":
        await update_account_from_session_check(
            TelegramSessionCheckResult(
                account_id=account_id,
                session_path=f"sessions/{account_id}",
                status=status,
                is_temporary=False,
            ),
        )


async def _proxied(account_id: str, port: int, country: str, name: str) -> None:
    proxy_id = await seed_account_proxy(account_id, port=port)
    await update_proxy_check(
        ProxyCheckUpdate(
            proxy_id=proxy_id, status="tcp_working", country_code=country, country_name=name
        )
    )


def _ids(page: AccountPage) -> list[str]:
    return sorted(item.account_id for item in page.items)


@pytest.mark.asyncio
async def test_status_filters_by_the_stat_tile_buckets() -> None:
    await _account("alive", "alive")
    await _account("flood", "flood_wait")
    await _account("fresh")
    await _account("unauth", "unauthorized")
    await _account("frozen", "frozen")
    await _account("broken", "session_error")

    assert _ids(await list_accounts_page(status="active")) == ["alive"]
    assert _ids(await list_accounts_page(status="idle")) == ["flood"]
    assert _ids(await list_accounts_page(status="needs_code")) == ["fresh", "unauth"]
    assert _ids(await list_accounts_page(status="problem")) == ["broken", "frozen"]
    # A raw status still filters exactly, as before the buckets.
    assert _ids(await list_accounts_page(status="frozen")) == ["frozen"]
    page = await list_accounts_page(status="problem")
    assert page.total == 2


@pytest.mark.asyncio
async def test_proxy_country_filters_and_options_come_from_data() -> None:
    await _account("de", phone="+4915112345678")
    await _account("nl", phone="+31612345678")
    await _account("bare", phone="+79991112233")
    await _proxied("de", 1081, "de", "Germany")
    await _proxied("nl", 1082, "NL", "Netherlands")

    assert _ids(await list_accounts_page(proxy_country="DE")) == ["de"]
    assert _ids(await list_accounts_page(proxy_country="nl")) == ["nl"]
    assert _ids(await list_accounts_page(proxy_country="none")) == ["bare"]

    options = await account_filter_options()
    assert [(o.country_code, o.country_name) for o in options.proxy_countries] == [
        ("DE", "Germany"),
        ("NL", "Netherlands"),
    ]
    assert options.no_proxy_count == 1
    assert [o.calling_code for o in options.phone_codes] == [7, 31, 49]


@pytest.mark.asyncio
async def test_phone_code_and_trust_filter_in_python_and_still_paginate() -> None:
    for index in range(5):
        await _account(f"ru{index}", phone=f"+7999111220{index}")
    await _account("de", phone="+4915112345678")

    first = await list_accounts_page(phone_code=7, limit=2)
    assert (len(first.items), first.total) == (2, 5)
    assert first.next_cursor is not None
    rest = await list_accounts_page(phone_code=7, limit=10, cursor=first.next_cursor)
    assert (len(rest.items), rest.next_cursor) == (3, None)
    assert {a.account_id for a in first.items} | {a.account_id for a in rest.items} == {
        f"ru{index}" for index in range(5)
    }
    # Signals and folders are attached on this path too.
    assert all(a.trust_score is not None for a in rest.items)

    await upsert_spam_status(
        SpamStatusVerdict(
            account_id="de",
            status="limited",
            detail="restricted",
            checked_at="2026-06-30T00:00:00+00:00",
        ),
    )
    scores = {a.account_id: a.trust_score for a in (await list_accounts_page()).items}
    assert scores["de"] is not None
    assert scores["ru0"] is not None
    assert scores["de"] < scores["ru0"]
    trusted = await list_accounts_page(min_trust=scores["ru0"])
    assert "de" not in _ids(trusted)
    assert trusted.total == 5
    assert _ids(await list_accounts_page(min_trust=0)) == sorted([*scores])
