"""Warming tests split from the former service test module: test_persistence.py."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import delete as sa_delete

from core.db import (
    _accounts,
    _get_engine,
    _warming_account_state,
    create_account,
    fetch_warming_state,
    upsert_spam_status,
    upsert_warming_state,
)
from schemas.accounts import AccountCreate
from schemas.spam_status import SpamStatusVerdict
from schemas.warming import (
    WarmingStateWrite,
)
from tests.factories import seed_account_proxy

if TYPE_CHECKING:
    from pathlib import Path

from tests.services.warming._support import (
    _exercise_migration_seven,
)


@pytest.mark.asyncio
async def test_joined_channels_cleanup_on_account_delete() -> None:
    import asyncio  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415

    from core.db import (  # noqa: PLC0415
        _warming_joined_channels,
        add_warming_channel,
        record_channel_joined,
    )
    from core.repositories.accounts import _delete_account  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-a"))
    await add_warming_channel("testchan")
    await record_channel_joined("acc-a", "testchan")

    # Remove account
    await asyncio.to_thread(_delete_account, "acc-a")

    # Verify cascade delete
    with _get_engine().connect() as conn:
        res = conn.execute(select(_warming_joined_channels)).all()
        assert len(res) == 0


@pytest.mark.asyncio
async def test_delete_account_with_all_related_rows() -> None:
    """F4 regression: deleting a warmed account must not raise IntegrityError.

    Schema declares ForeignKey on warming_account_state / account_spam_status
    without ON DELETE CASCADE, so the repo has to clean children explicitly. We
    seed every per-account table that exists. The shared pool proxy is NOT a
    child (accounts.proxy_id → proxies.id) and must survive the deletion.
    """
    from core.db import (  # noqa: PLC0415
        _account_spam_status,
        _device_fingerprints,
        _warming_joined_channels,
        add_warming_channel,
        list_proxies,
        record_channel_joined,
        upsert_warming_state,
    )
    from core.repositories.accounts import _delete_account  # noqa: PLC0415
    from core.repositories.dialogues import (  # noqa: PLC0415
        dialogue_messages,
        dialogue_pairs,
        record_dialogue_message,
        replace_dialogue_pairs,
    )

    await create_account(AccountCreate(account_id="acc-a", session_name="acc-a"))
    await create_account(AccountCreate(account_id="acc-b", session_name="acc-b"))
    await add_warming_channel("testchan")
    await record_channel_joined("acc-a", "testchan")
    await upsert_warming_state(WarmingStateWrite(account_id="acc-a", state="active"))
    await seed_account_proxy("acc-a")
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id="acc-a",
            status="clean",
            detail=None,
            checked_at="2026-01-01T00:00:00+00:00",
        ),
    )
    await replace_dialogue_pairs([("acc-a", "acc-b")])
    await record_dialogue_message("acc-a", "acc-b", "hi")
    await record_dialogue_message("acc-b", "acc-a", "yo")

    await asyncio.to_thread(_delete_account, "acc-a")

    with _get_engine().connect() as conn:
        assert (
            conn.execute(sa_delete(_accounts).where(_accounts.c.account_id == "acc-a")).rowcount
            == 0
        )
        assert (
            conn.execute(
                _warming_joined_channels.select().where(
                    _warming_joined_channels.c.account_id == "acc-a",
                ),
            ).all()
            == []
        )
        assert (
            conn.execute(
                _warming_account_state.select().where(
                    _warming_account_state.c.account_id == "acc-a",
                ),
            ).all()
            == []
        )
        assert (
            conn.execute(
                _account_spam_status.select().where(
                    _account_spam_status.c.account_id == "acc-a",
                ),
            ).all()
            == []
        )
        assert (
            conn.execute(
                _device_fingerprints.select().where(
                    _device_fingerprints.c.account_id == "acc-a",
                ),
            ).all()
            == []
        )
        assert (
            conn.execute(
                dialogue_messages.select().where(
                    (dialogue_messages.c.from_account == "acc-a")
                    | (dialogue_messages.c.to_account == "acc-a"),
                ),
            ).all()
            == []
        )
        assert (
            conn.execute(
                dialogue_pairs.select().where(
                    (dialogue_pairs.c.account_a == "acc-a")
                    | (dialogue_pairs.c.account_b == "acc-a"),
                ),
            ).all()
            == []
        )

    # The shared pool proxy is not a child — it must outlive the deleted account.
    assert len((await list_proxies()).proxies) == 1


@pytest.mark.asyncio
async def test_create_account_rejects_duplicate_session_name() -> None:
    """F5: two accounts cannot share one Telethon session file."""
    from core.repositories.accounts import DuplicateSessionNameError  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1", session_name="shared"))
    with pytest.raises(DuplicateSessionNameError):
        await create_account(AccountCreate(account_id="acc-2", session_name="shared"))


@pytest.mark.asyncio
async def test_create_account_allows_multiple_null_session_names() -> None:
    """F5: NULL session_name is not a value, so accounts without one can coexist."""
    await create_account(AccountCreate(account_id="acc-1"))
    await create_account(AccountCreate(account_id="acc-2"))


@pytest.mark.asyncio
async def test_concurrent_set_state_increment_cycle_preserves_all_increments() -> None:
    """P2.4: N parallel _set_state(increment_cycle=True) → cycles_completed == N.

    The pre-fix code computed ``cycles + 1`` from a stale read in _set_state and
    handed the result to the upsert. Concurrent writers all read the same
    pre-state and clobbered each other (each thought their write was the next).
    The fix moves the bump into the ON CONFLICT DO UPDATE SQL expression.
    """
    from services.warming._state import _set_state  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))

    n_writers = 8

    async def bump(i: int) -> None:
        await _set_state("acc-1", "sleeping", last_event=f"cycle-{i}", increment_cycle=True)

    await asyncio.gather(*(bump(i) for i in range(n_writers)))
    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.cycles_completed == n_writers


@pytest.mark.asyncio
async def test_migration_unique_session_name_handles_existing_duplicates(
    tmp_path: Path,
) -> None:
    """P1.3: migration #7 must auto-remediate legacy duplicates, not crash startup.

    Re-creates the pre-migration shape (no unique index) with two rows that
    share a session_name, then drives ``apply_migrations`` and asserts the
    index is in place and the second row's session_name was nulled.
    """
    from sqlalchemy import create_engine  # noqa: PLC0415

    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    try:
        await _exercise_migration_seven(engine)
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_create_account_post_integrity_branch_raises_domain_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2.5: post-IntegrityError branch raises DuplicateSessionNameError.

    The IntegrityError-on-INSERT branch translates the unique-index violation
    into DuplicateSessionNameError, not RuntimeError.

    Drives the post-IntegrityError path deterministically by patching the
    cooperative pre-check SELECT to return None, so the INSERT actually fires
    and the migration-#7 unique index raises IntegrityError. The fix must
    re-read after the IntegrityError, find the conflict, and surface the
    typed domain error — never the catch-all "Account was not persisted".

    A live asyncio.gather race would be the ideal regression test, but
    SQLite + WAL + busy_timeout produces non-deterministic OperationalError
    ('database is locked') under thread contention, so we stick to the
    deterministic unit shape.
    """
    from core.repositories import accounts as accounts_repo  # noqa: PLC0415
    from core.repositories.accounts import DuplicateSessionNameError  # noqa: PLC0415

    # Plant the conflicting row first.
    await create_account(AccountCreate(account_id="acc-1", session_name="shared"))

    # Patch the pre-check SELECT to always return None so the second create
    # proceeds straight to INSERT and trips the unique index.
    original_create = accounts_repo._create_account

    def patched_create(data):  # type: ignore[no-untyped-def]
        # Temporarily blind the pre-check by patching select() inside the
        # accounts module to return a SELECT that yields no rows on .first().
        original_select = accounts_repo.select
        call_count = {"n": 0}

        class _NullFirst:
            def first(self) -> None:
                return None

        def _select_returning_null(*args, **kwargs):  # type: ignore[no-untyped-def]
            call_count["n"] += 1
            if call_count["n"] == 1:
                # The first select() call inside _create_account is the
                # pre-check. Return a SELECT that genuinely won't match the
                # conflict so the INSERT path runs.
                return original_select(
                    accounts_repo._accounts.c.account_id,
                ).where(accounts_repo._accounts.c.account_id == "__no_such_id__")
            return original_select(*args, **kwargs)

        monkeypatch.setattr(accounts_repo, "select", _select_returning_null)
        try:
            return original_create(data)
        finally:
            monkeypatch.setattr(accounts_repo, "select", original_select)

    monkeypatch.setattr(accounts_repo, "_create_account", patched_create)

    with pytest.raises(DuplicateSessionNameError):
        await create_account(AccountCreate(account_id="acc-2", session_name="shared"))


@pytest.mark.asyncio
async def test_migration_duplicate_session_name_marks_nulled_accounts_not_alive(
    tmp_path: Path,
) -> None:
    """Round-4 P2.3: nulling a duplicate session_name must also flip status.

    Without flipping status, an account left as ``alive`` after losing its
    session_name silently switches its session file path (``_session_path``
    falls back to account_id when session_name is None) — the operator
    thinks the account is healthy while every runtime action talks to a
    different session file.
    """
    from sqlalchemy import create_engine  # noqa: PLC0415

    from core.migrations import apply_migrations  # noqa: PLC0415

    db_path = tmp_path / "legacy_alive.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE accounts ("
                "  account_id VARCHAR PRIMARY KEY,"
                "  label VARCHAR,"
                "  session_name VARCHAR,"
                "  status VARCHAR NOT NULL,"
                "  created_at VARCHAR NOT NULL,"
                "  updated_at VARCHAR NOT NULL"
                ")",
            )
            connection.exec_driver_sql(
                "INSERT INTO accounts (account_id, session_name, status, created_at, updated_at) "
                "VALUES ('acc-1', 'shared', 'alive', '2026-01-01', '2026-01-01')",
            )
            connection.exec_driver_sql(
                "INSERT INTO accounts (account_id, session_name, status, created_at, updated_at) "
                "VALUES ('acc-2', 'shared', 'alive', '2026-01-02', '2026-01-02')",
            )
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name VARCHAR NOT NULL, "
                "applied_at VARCHAR NOT NULL)",
            )
            for version in (1, 2, 3, 4, 5, 6, 8, 10, 16):
                connection.exec_driver_sql(
                    "INSERT INTO schema_version VALUES (?, 'stub', '2026-01-01')",
                    (version,),
                )

        apply_migrations(engine)

        with engine.connect() as connection:
            rows = connection.exec_driver_sql(
                "SELECT account_id, session_name, status FROM accounts ORDER BY account_id",
            ).all()
            by_id = {str(row[0]): row for row in rows}
            assert by_id["acc-1"][1] == "shared"  # oldest kept its name
            assert by_id["acc-1"][2] == "alive"  # and its status
            assert by_id["acc-2"][1] is None  # duplicate nulled
            assert by_id["acc-2"][2] != "alive"  # AND demoted so operator re-checks
            assert by_id["acc-2"][2] == "new"
    finally:
        engine.dispose()
