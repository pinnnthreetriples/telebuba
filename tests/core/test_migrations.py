"""Tests for the SQLite migration registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine

from core.db import _get_engine, configure_database  # type: ignore[attr-defined]
from core.migrations import MIGRATIONS, apply_migrations

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


def test_apply_migrations_stamps_every_version() -> None:
    """A fresh DB ends up with one ``schema_version`` row per migration."""
    engine = _get_engine()
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT version FROM schema_version ORDER BY version",
        ).all()
    versions = [int(row[0]) for row in rows]
    assert versions == [v for v, _name, _fn in MIGRATIONS]


def test_apply_migrations_is_idempotent() -> None:
    """Re-applying the registry does not duplicate version rows or fail."""
    engine = _get_engine()
    apply_migrations(engine)
    apply_migrations(engine)
    with engine.connect() as connection:
        count = connection.exec_driver_sql("SELECT COUNT(*) FROM schema_version").scalar_one()
    assert int(count) == len(MIGRATIONS)


def test_legacy_database_is_brought_up_to_date(tmp_path: Path) -> None:
    """An old DB missing ``schema_version`` gets stamped, not re-altered."""
    # Simulate an old database: create just the accounts table without
    # `bio`, and no schema_version table. apply_migrations should stamp it
    # and add the missing column.
    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE accounts ("
            "account_id VARCHAR PRIMARY KEY,"
            "label VARCHAR,"
            "session_name VARCHAR,"
            "status VARCHAR NOT NULL,"
            "user_id BIGINT,"
            "phone VARCHAR,"
            "username VARCHAR,"
            "first_name VARCHAR,"
            "last_name VARCHAR,"
            "last_checked_at VARCHAR,"
            "created_at VARCHAR NOT NULL,"
            "updated_at VARCHAR NOT NULL"
            ")",
        )
        connection.exec_driver_sql(
            "CREATE TABLE account_proxies ("
            "account_id VARCHAR PRIMARY KEY,"
            "proxy_type VARCHAR NOT NULL,"
            "host VARCHAR NOT NULL,"
            "port INTEGER NOT NULL,"
            "status VARCHAR NOT NULL,"
            "created_at VARCHAR NOT NULL,"
            "updated_at VARCHAR NOT NULL"
            ")",
        )
        connection.exec_driver_sql(
            "CREATE TABLE warming_account_state ("
            "account_id VARCHAR PRIMARY KEY,"
            "state VARCHAR NOT NULL,"
            "cycles_completed INTEGER NOT NULL,"
            "updated_at VARCHAR NOT NULL"
            ")",
        )
        connection.exec_driver_sql(
            "CREATE TABLE warming_settings ("
            "id INTEGER PRIMARY KEY,"
            "inter_account_chat INTEGER NOT NULL,"
            "reactions_enabled INTEGER NOT NULL,"
            "gemini_api_key VARCHAR NOT NULL,"
            "gemini_model VARCHAR NOT NULL,"
            "updated_at VARCHAR NOT NULL"
            ")",
        )

    apply_migrations(engine)
    with engine.connect() as connection:
        applied = connection.exec_driver_sql(
            "SELECT version FROM schema_version ORDER BY version",
        ).all()
        bio_present = connection.exec_driver_sql("PRAGMA table_info(accounts)").mappings().all()
    engine.dispose()
    assert [int(row[0]) for row in applied] == [v for v, _n, _f in MIGRATIONS]
    assert any(row["name"] == "bio" for row in bio_present)


def test_append_only_versions_are_unique() -> None:
    """Two migrations sharing the same version would silently mask each other."""
    versions = [v for v, _name, _fn in MIGRATIONS]
    assert len(versions) == len(set(versions))
    # Should also be a strictly increasing sequence — catches an off-by-one.
    assert versions == sorted(versions)
