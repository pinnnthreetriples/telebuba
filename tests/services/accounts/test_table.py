"""Account lifecycle, table, health, and geo service tests."""

from __future__ import annotations

import pytest

from core.config import settings
from core.db import (
    update_account_from_session_check,
    update_proxy_check,
    upsert_spam_status,
)
from schemas.accounts import (
    AccountCheckRequest,
    AccountCreate,
    AccountStatus,
    health_for_status,
)
from schemas.proxy import ProxyCheckUpdate
from schemas.spam_status import SpamStatusVerdict
from schemas.telegram_session import TelegramSessionCheckResult
from services.accounts import (
    account_stats,
    add_account,
    check_account_session,
    evaluate_account_geo,
    list_accounts_page,
    list_listener_accounts,
    remove_account,
)
from tests.factories import seed_account_proxy


@pytest.mark.asyncio
async def test_add_account_creates_fingerprint_and_page_row() -> None:
    account = await add_account(
        AccountCreate(account_id="account-1", label="Main", session_name="session-1"),
    )
    page = await list_accounts_page()
    stats = await account_stats()

    assert account.account_id == "account-1"
    assert stats.total == 1
    assert stats.needs_code == 1  # a never-checked account still needs a login code
    row = page.items[0]
    assert row.label == "Main"
    assert row.device_model is not None
    # A freshly added account has not been checked yet -> warn (amber).
    assert health_for_status(row.status) == "warn"


@pytest.mark.asyncio
async def test_remove_account_unlinks_session_file() -> None:
    """Deleting an account must unlink its orphaned Telethon ``.session`` file.

    The path is ``session_dir/<session_name>.session`` (session_name, not the
    account_id), matching how the client builder resolves it.
    """
    await add_account(AccountCreate(account_id="acc-del", session_name="sess-del"))
    session_file = settings.telegram.session_dir / "sess-del.session"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_bytes(b"sqlite session bytes")

    await remove_account("acc-del")

    assert not session_file.exists()


@pytest.mark.asyncio
async def test_list_listener_accounts_keeps_only_live_sessions() -> None:
    """Only accounts with an authorized session may act as the neurocomment listener."""
    await add_account(AccountCreate(account_id="never", label="Never checked"))
    await add_account(AccountCreate(account_id="live", label="Live"))
    await add_account(AccountCreate(account_id="dead", label="No session"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="live",
            session_path="sessions/live",
            status="alive",
            is_temporary=False,
        ),
    )
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="dead",
            session_path="sessions/dead",
            status="session_error",
            is_temporary=False,
            error_type="AuthKeyError",
            error_message="session revoked",
        ),
    )

    result = await list_listener_accounts()

    # "never" (status new → warn) and "dead" (session_error → fail) are excluded.
    assert [a.account_id for a in result.accounts] == ["live"]


@pytest.mark.asyncio
async def test_list_accounts_page_filters_query_and_status() -> None:
    await add_account(AccountCreate(account_id="one", label="Alpha"))
    await add_account(AccountCreate(account_id="two", label="Beta"))

    query_page = await list_accounts_page(query="alp")
    status_page = await list_accounts_page(status="alive")

    assert [row.account_id for row in query_page.items] == ["one"]
    assert status_page.items == []
    # Stats stay fleet-wide regardless of the filtered page.
    assert (await account_stats()).total == 2


@pytest.mark.asyncio
async def test_list_accounts_page_paginates_by_cursor() -> None:
    """limit/cursor must reach the DB — the UI never loads everything to slice in Python."""
    for ident in ("a", "b", "c", "d", "e"):
        await add_account(AccountCreate(account_id=ident, label=f"Label {ident}"))

    page = await list_accounts_page(limit=2)
    assert len(page.items) == 2
    assert page.next_cursor is not None

    next_page = await list_accounts_page(limit=2, cursor=page.next_cursor)
    assert len(next_page.items) == 2
    assert {row.account_id for row in page.items} & {
        row.account_id for row in next_page.items
    } == set()


async def _set_status(account_id: str, status: AccountStatus) -> None:
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id=account_id,
            session_path=f"sessions/{account_id}",
            status=status,  # ty: ignore[invalid-argument-type] — SessionCheckStatus ⊇ used set
            is_temporary=False,
        ),
    )


@pytest.mark.asyncio
async def test_account_stats_counts_whole_fleet_across_pages() -> None:
    """Stats span the entire table (one grouped query), not a single 20-row page.

    Seeds >1 page of accounts across every design bucket and asserts the tile
    counts are the fleet-wide totals, independent of pagination.
    """
    # 10 alive, 6 flood_wait (idle/spam), 5 unauthorized + 4 new (needs_code),
    # 3 session_error + 2 account_error + 2 frozen (problem) = 32 accounts (> one page).
    plan: list[tuple[str, AccountStatus, int]] = [
        ("alive", "alive", 10),
        ("flood", "flood_wait", 6),
        ("unauth", "unauthorized", 5),
        ("new", "new", 4),
        ("serr", "session_error", 3),
        ("aerr", "account_error", 2),
        ("frozen", "frozen", 2),
    ]
    for prefix, status, count in plan:
        for i in range(count):
            ident = f"{prefix}-{i}"
            await add_account(AccountCreate(account_id=ident, label=ident))
            if status != "new":  # "new" is the create default; no flip needed.
                await _set_status(ident, status)

    stats = await account_stats()

    assert stats.total == 32
    assert stats.active == 10  # alive
    assert stats.idle == 6  # flood_wait (spam-limited)
    assert stats.needs_code == 9  # 5 unauthorized + 4 new
    assert stats.problem == 7  # 3 session_error + 2 account_error + 2 frozen

    # Independence from pagination: a single page never sees the whole fleet.
    first_page = await list_accounts_page(limit=20)
    assert len(first_page.items) == 20
    assert first_page.next_cursor is not None
    assert stats.total > len(first_page.items)


@pytest.mark.asyncio
async def test_list_accounts_page_enriches_trust_and_spam() -> None:
    """The accounts page carries a computed Trust Score + last cached spam verdict."""
    await add_account(AccountCreate(account_id="limited"))
    await add_account(AccountCreate(account_id="unprobed"))
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id="limited",
            status="limited",
            detail="restricted until 2026",
            checked_at="2026-06-30T00:00:00+00:00",
        ),
    )

    page = await list_accounts_page()
    rows = {row.account_id: row for row in page.items}

    # Trust is computed for every row regardless of whether a spam probe ran.
    assert rows["limited"].trust_score is not None
    assert rows["limited"].trust_band is not None
    assert rows["unprobed"].trust_score is not None

    # The cached spam verdict surfaces on the probed row and docks its score.
    assert rows["limited"].spam_status == "limited"
    assert rows["limited"].spam_detail == "restricted until 2026"
    assert rows["unprobed"].spam_status is None
    assert rows["limited"].trust_score < rows["unprobed"].trust_score

    # The device fingerprint's system language is surfaced for the edit card.
    assert rows["limited"].device_lang is not None


@pytest.mark.asyncio
async def test_list_accounts_page_search_uses_db_filter() -> None:
    await add_account(AccountCreate(account_id="alpha", label="Alpha"))
    await add_account(AccountCreate(account_id="beta", label="Beta"))
    await add_account(AccountCreate(account_id="alphabet", label="Alphabet"))

    page = await list_accounts_page(query="alpha")
    ids = {row.account_id for row in page.items}
    assert ids == {"alpha", "alphabet"}
    # Stats stay the whole table — search narrows the page, not the totals.
    assert (await account_stats()).total == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_health"),
    [
        ("alive", "ok"),
        ("new", "warn"),
        ("flood_wait", "warn"),
        ("network_error", "warn"),
        ("proxy_error", "warn"),
        ("unknown_error", "warn"),
        ("unauthorized", "fail"),
        ("session_error", "fail"),
        ("account_error", "fail"),
        ("frozen", "fail"),
    ],
)
async def test_health_taxonomy_matches_status(
    monkeypatch: pytest.MonkeyPatch,
    status: AccountStatus,
    expected_health: str,
) -> None:
    """Every AccountStatus maps to exactly one of ok / warn / fail."""
    await add_account(AccountCreate(account_id="acc-h"))
    if status == "new":
        page = await list_accounts_page()
        assert health_for_status(page.items[0].status) == expected_health
        # Row carries the RAW status enum — the UI translates it to RU once.
        assert page.items[0].status == "new"
        return

    async def fake_check(_request: object) -> TelegramSessionCheckResult:
        return TelegramSessionCheckResult(
            account_id="acc-h",
            session_path="sessions/acc-h",
            status=status,
            is_temporary=status not in {"alive", "unauthorized", "session_error", "account_error"},
        )

    monkeypatch.setattr("services.accounts.sessions.check_telegram_session", fake_check)
    await check_account_session(AccountCheckRequest(account_id="acc-h"))
    page = await list_accounts_page()
    assert health_for_status(page.items[0].status) == expected_health
    # Row carries the RAW status enum (e.g. "network_error"), not an English
    # label — the UI is the single translation point. Guards the regression
    # where the service emitted "Network"/"Proxy"/"Unknown" and the UI RU map
    # (keyed "Network error"/…) silently failed to translate them.
    assert page.items[0].status == status


@pytest.mark.asyncio
async def test_evaluate_account_geo_flags_mismatch() -> None:
    await add_account(AccountCreate(account_id="acc-1"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="acc-1",
            session_path="acc-1",
            status="alive",
            is_temporary=False,
            phone="+77011234567",
        ),
    )
    proxy_id = await seed_account_proxy("acc-1", host="h")
    await update_proxy_check(
        ProxyCheckUpdate(proxy_id=proxy_id, status="tcp_working", country_code="US"),
    )

    verdict = await evaluate_account_geo("acc-1")

    assert verdict.status == "mismatch"
    assert verdict.phone_country == "KZ"
    assert verdict.proxy_country == "US"


@pytest.mark.asyncio
async def test_evaluate_account_geo_unknown_for_missing_account() -> None:
    verdict = await evaluate_account_geo("ghost")
    assert verdict.status == "unknown"
