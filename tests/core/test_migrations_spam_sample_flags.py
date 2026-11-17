"""Migration 118 — the sample flags match ``create_all``; additive; idempotent; old rows read 0."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.db import _metadata
from core.migration_steps_spam_sample_flags import _add_spam_sample_flags
from core.migrations import MIGRATIONS

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

# ``spam_reply_samples`` as migration 114 created it, before the flags.
_OLD_SAMPLES = (
    "CREATE TABLE spam_reply_samples ("
    " text_hash VARCHAR NOT NULL, sample VARCHAR NOT NULL, lang VARCHAR,"
    " source VARCHAR DEFAULT 'probe' NOT NULL, legacy_status VARCHAR, strict_status VARCHAR,"
    " has_date INTEGER DEFAULT 0 NOT NULL, first_seen_at VARCHAR NOT NULL,"
    " last_seen_at VARCHAR NOT NULL, seen_count INTEGER DEFAULT 1 NOT NULL,"
    " PRIMARY KEY (text_hash))"
)


def _columns(engine: Engine) -> dict[str, tuple[object, ...]]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql("PRAGMA table_info(spam_reply_samples)").mappings().all()
    return {r["name"]: (r["type"], r["notnull"], r["dflt_value"], r["pk"]) for r in rows}


def test_the_registry_carries_the_migration_once_and_in_order() -> None:
    entries = [e for e in MIGRATIONS if e[2] is _add_spam_sample_flags]
    assert entries == [(118, "add_spam_sample_flags", _add_spam_sample_flags)]
    versions = [version for version, _name, _body in MIGRATIONS]
    assert len(versions) == len(set(versions))
    assert versions == sorted(versions)


def test_created_and_migrated_schemas_match(legacy_engine: _EngineFactory) -> None:
    created = legacy_engine("created.db")
    _metadata.create_all(created)
    migrated = legacy_engine("migrated.db")
    with migrated.begin() as connection:
        connection.exec_driver_sql(_OLD_SAMPLES)
    for _ in range(2):  # idempotent
        with migrated.begin() as connection:
            _add_spam_sample_flags(connection)

    assert _columns(migrated) == _columns(created)
    assert {"multi_message", "has_markup"} <= set(_columns(migrated))


def test_old_rows_read_zero_and_are_otherwise_untouched(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("old.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(_OLD_SAMPLES)
        connection.exec_driver_sql(
            "INSERT INTO spam_reply_samples (text_hash, sample, has_date, first_seen_at,"
            " last_seen_at) VALUES ('h', 'words', 1, 't1', 't2')"
        )
        _add_spam_sample_flags(connection)
    with engine.connect() as connection:
        row = connection.exec_driver_sql("SELECT * FROM spam_reply_samples").mappings().one()
    assert (row["sample"], row["has_date"]) == ("words", 1)
    assert (row["multi_message"], row["has_markup"]) == (0, 0)


def test_a_database_without_the_samples_table_is_left_alone(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("bare.db")
    with engine.begin() as connection:
        _add_spam_sample_flags(connection)
    assert _columns(engine) == {}
