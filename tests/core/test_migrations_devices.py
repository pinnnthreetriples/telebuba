"""Migration 88 — an account may hold a desktop and a mobile authorization.

The legacy database here is the pre-88 shape of the three tables the step touches,
stamped with every earlier version, so ``apply_migrations`` runs exactly migration 88
the way a live upgrade does. Every existing account must come out as what it already was —
one desktop device with its fingerprint untouched — and a second run must change
nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from core.db import _metadata
from core.migration_steps_devices import _add_account_devices
from core.migrations import MIGRATIONS, apply_migrations
from tests.core.test_migrations_scheduled_posts import _shape

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_V87_TABLES = (
    "CREATE TABLE accounts ("
    "  account_id VARCHAR NOT NULL PRIMARY KEY, label VARCHAR, session_name VARCHAR,"
    "  status VARCHAR NOT NULL, phone VARCHAR)",
    "CREATE UNIQUE INDEX ix_accounts_session_name_unique ON accounts (session_name)",
    "CREATE TABLE device_fingerprints ("
    "  account_id VARCHAR NOT NULL PRIMARY KEY, platform VARCHAR NOT NULL,"
    "  device_model VARCHAR NOT NULL, system_version VARCHAR NOT NULL,"
    "  app_version VARCHAR NOT NULL, lang_code VARCHAR NOT NULL,"
    "  system_lang_code VARCHAR NOT NULL)",
    "CREATE TABLE warming_account_state ("
    "  account_id VARCHAR NOT NULL PRIMARY KEY, state VARCHAR NOT NULL,"
    "  activity_persona VARCHAR NOT NULL DEFAULT 'normal')",
)
_FINGERPRINT = ("acc-1", "windows", "Desktop PC", "Windows 11", "5.6.3 x64", "es", "es-MX")


@pytest.fixture
def v87_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("v87.db")
    with engine.begin() as connection:
        for statement in _V87_TABLES:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name VARCHAR NOT NULL,"
            " applied_at VARCHAR NOT NULL)",
        )
        now = datetime.now(UTC).isoformat()
        for version, name, _body in MIGRATIONS:
            if version < 88:
                connection.exec_driver_sql(
                    "INSERT INTO schema_version VALUES (?, ?, ?)",
                    (version, name, now),
                )
        connection.exec_driver_sql(
            "INSERT INTO accounts VALUES ('acc-1', 'A', 'acc-1', 'alive', '+5215550000')",
        )
        connection.exec_driver_sql(
            "INSERT INTO device_fingerprints VALUES (?, ?, ?, ?, ?, ?, ?)",
            _FINGERPRINT,
        )
        connection.exec_driver_sql(
            "INSERT INTO warming_account_state VALUES ('acc-1', 'sleeping', 'calm')",
        )
    return engine


def _rows(engine: Engine) -> dict[str, list[tuple[object, ...]]]:
    with engine.connect() as connection:
        run = connection.exec_driver_sql
        return {
            "fingerprints": [
                tuple(row)
                for row in run(
                    "SELECT account_id, device_type, platform, device_model, system_version,"
                    " app_version, lang_code, system_lang_code FROM device_fingerprints",
                )
            ],
            "accounts": [
                tuple(row)
                for row in run(
                    "SELECT account_id, session_name, session_device, second_session_name,"
                    " status, phone FROM accounts",
                )
            ],
            "warming": [
                tuple(row)
                for row in run(
                    "SELECT account_id, state, activity_persona, device_mode"
                    " FROM warming_account_state",
                )
            ],
        }


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_account_devices]
    assert entries == [(88, "add_account_devices", _add_account_devices)]


def test_a_v87_account_becomes_one_desktop_device_with_its_data_intact(
    v87_engine: Engine,
) -> None:
    apply_migrations(v87_engine)

    rows = _rows(v87_engine)
    account_id, *fingerprint = _FINGERPRINT
    assert rows["fingerprints"] == [(account_id, "desktop", *fingerprint)]
    assert rows["accounts"] == [("acc-1", "acc-1", "desktop", None, "alive", "+5215550000")]
    assert rows["warming"] == [("acc-1", "sleeping", "calm", "desktop")]


def test_a_second_run_changes_nothing(v87_engine: Engine) -> None:
    apply_migrations(v87_engine)
    before = (_rows(v87_engine), _shape(v87_engine, "device_fingerprints"))

    with v87_engine.begin() as connection:
        _add_account_devices(connection)
    apply_migrations(v87_engine)

    assert (_rows(v87_engine), _shape(v87_engine, "device_fingerprints")) == before


def test_the_fingerprint_key_admits_one_row_per_device(v87_engine: Engine) -> None:
    apply_migrations(v87_engine)
    with v87_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO device_fingerprints VALUES"
            " ('acc-1', 'mobile', 'android', 'GooglePixel 8', 'SDK 34', '12.10.6 (7112)',"
            " 'es', 'es-MX')",
        )
    assert {row[1] for row in _rows(v87_engine)["fingerprints"]} == {"desktop", "mobile"}


@pytest.mark.parametrize("table", ["device_fingerprints", "warming_account_state"])
def test_migrated_columns_match_a_fresh_database(
    legacy_engine: _EngineFactory,
    v87_engine: Engine,
    table: str,
) -> None:
    created = legacy_engine("created.db")
    _metadata.create_all(created)
    apply_migrations(v87_engine)

    fresh = cast("dict[str, object]", _shape(created, table)["columns"])
    migrated = cast("dict[str, object]", _shape(v87_engine, table)["columns"])
    assert {name: fresh[name] for name in migrated} == migrated


def test_the_second_session_name_is_unique_like_the_first(v87_engine: Engine) -> None:
    apply_migrations(v87_engine)
    indexes = cast(
        "dict[str, tuple[int, str, tuple[str, ...]]]", _shape(v87_engine, "accounts")["indexes"]
    )
    assert indexes["ix_accounts_second_session_name_unique"][0] == 1
