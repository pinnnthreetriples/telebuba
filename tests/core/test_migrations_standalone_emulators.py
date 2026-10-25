"""Migrations 101 and 102 — ``standalone_emulators`` and its apps, in parity with ``create_all``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import _metadata
from core.migration_steps_standalone_emulators import (
    _add_standalone_emulators,
    _add_standalone_install_apps,
)
from core.migrations import MIGRATIONS
from tests.core.test_migrations_scheduled_posts import _shape

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]


@pytest.fixture
def created_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("created.db")
    _metadata.create_all(engine)
    return engine


@pytest.fixture
def migrated_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("migrated.db")
    with engine.begin() as connection:
        _add_standalone_emulators(connection)
        _add_standalone_install_apps(connection)
    return engine


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_standalone_emulators]
    assert entries == [(101, "add_standalone_emulators", _add_standalone_emulators)]
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_standalone_install_apps]
    assert entries == [(102, "add_standalone_install_apps", _add_standalone_install_apps)]


def test_created_and_migrated_tables_match(created_engine: Engine, migrated_engine: Engine) -> None:
    assert _shape(created_engine, "standalone_emulators") == _shape(
        migrated_engine, "standalone_emulators"
    )


def test_the_migration_is_idempotent(migrated_engine: Engine) -> None:
    before = _shape(migrated_engine, "standalone_emulators")
    with migrated_engine.begin() as connection:
        _add_standalone_emulators(connection)
        _add_standalone_install_apps(connection)
    assert _shape(migrated_engine, "standalone_emulators") == before


def test_a_phone_made_before_the_apps_keeps_installing_telegram(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("old.db")
    with engine.begin() as connection:
        _add_standalone_emulators(connection)
        connection.exec_driver_sql(
            "INSERT INTO standalone_emulators VALUES ('e1', NULL, NULL, 'm', 's', 'a', 'ru',"
            " 'ru', '2026-10-25')"
        )
        _add_standalone_install_apps(connection)
        apps = connection.exec_driver_sql("SELECT install_apps FROM standalone_emulators").all()

    assert apps == [("telegram",)]
