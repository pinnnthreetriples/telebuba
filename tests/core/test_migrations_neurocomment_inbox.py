"""Migration #53 against a real pre-#53 database.

#53 is the only irreversible upgrade step in this change: besides creating the
inbox and cursor tables it ALTERs ``neurocomment_comments`` and back-fills a
reply deadline that no later code recomputes. These tests drive it through
``apply_migrations`` — the path an operator's database actually takes — rather
than calling the body on a bare table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import inspect

from core.db import configure_database
from core.migrations import MIGRATIONS, apply_migrations

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Connection, Engine

    from tests.core.conftest import _EngineFactory

_INBOX_VERSION = 53
_CREATED_AT = "2026-01-01T00:00:00+00:00"
_WAIT_MINUTES = 25


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


def _build_pre_inbox_database(connection: Connection) -> None:
    """A neurocomment database stamped up to #52 and nothing beyond it."""
    connection.exec_driver_sql(
        "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name VARCHAR NOT NULL, "
        "applied_at VARCHAR NOT NULL)",
    )
    for version, name, _body in MIGRATIONS:
        if version < _INBOX_VERSION:
            connection.exec_driver_sql(
                "INSERT INTO schema_version(version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, _CREATED_AT),
            )
    connection.exec_driver_sql(
        "CREATE TABLE neurocomment_settings (id INTEGER PRIMARY KEY, "
        "reply_wait_minutes INTEGER NOT NULL)",
    )
    connection.exec_driver_sql(
        "INSERT INTO neurocomment_settings VALUES (1, ?)",
        (_WAIT_MINUTES,),
    )
    connection.exec_driver_sql(
        "CREATE TABLE neurocomment_comments (channel VARCHAR NOT NULL, post_id INTEGER NOT NULL, "
        "campaign_id VARCHAR NOT NULL, account_id VARCHAR NOT NULL, status VARCHAR NOT NULL, "
        "comment_text VARCHAR, comment_msg_id INTEGER, created_at VARCHAR NOT NULL, "
        "updated_at VARCHAR NOT NULL, PRIMARY KEY (channel, post_id))",
    )
    for post_id, status in ((1, "waiting"), (2, "posted")):
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_comments VALUES (?, ?, 'camp', 'acc', ?, 'hi', 7, ?, ?)",
            ("@chan", post_id, status, _CREATED_AT, _CREATED_AT),
        )


def _reply_rows(engine: Engine) -> list[tuple[object, ...]]:
    with engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.exec_driver_sql(
                "SELECT post_id, reply_state, reply_stage, reply_outcome, reply_attempts, "
                "reply_deadline_at FROM neurocomment_comments ORDER BY post_id",
            ).all()
        ]


def test_pre_inbox_database_gains_the_inbox_tables_and_index(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("pre-53.db")
    with engine.begin() as connection:
        _build_pre_inbox_database(connection)

    apply_migrations(engine)

    inspector = inspect(engine)
    assert {"neurocomment_inbox", "neurocomment_cursors"} <= set(inspector.get_table_names())
    assert "ix_nc_inbox_state_date" in {
        str(index["name"]) for index in inspector.get_indexes("neurocomment_inbox")
    }
    with engine.connect() as connection:
        stamped = connection.exec_driver_sql(
            "SELECT name FROM schema_version WHERE version = ?",
            (_INBOX_VERSION,),
        ).scalar_one()
    assert stamped == "add_neurocomment_inbox"


def test_pre_inbox_waiting_comments_get_the_deadline_the_settings_row_asks_for(
    legacy_engine: _EngineFactory,
) -> None:
    """The deadline is frozen at upgrade time, so it must be right on the first pass.

    A comment already parked in reply mode on main has no deadline column; every
    sweep used to re-derive it. After #53 the stored value is the only one there is.
    """
    engine = legacy_engine("pre-53-backfill.db")
    with engine.begin() as connection:
        _build_pre_inbox_database(connection)

    apply_migrations(engine)

    assert _reply_rows(engine) == [
        (1, "waiting", "waiting", None, 0, "2026-01-01T00:25:00.000+00:00"),
        # Not waiting for a reader, so it is not in reply mode and gets no deadline.
        (2, None, None, None, 0, None),
    ]


def test_running_the_inbox_step_a_second_time_changes_nothing(
    legacy_engine: _EngineFactory,
) -> None:
    """The five ALTERs and the back-fill must survive a replay of the whole step.

    Un-stamping is how a replay happens in practice: a restored database, or a
    version row lost with the table around it. A second pass must not re-ALTER
    (SQLite would abort on the duplicate column) nor move a deadline a running
    sweep is already counting down to, even if the operator changed the knob since.
    """
    engine = legacy_engine("pre-53-idempotent.db")
    with engine.begin() as connection:
        _build_pre_inbox_database(connection)

    apply_migrations(engine)
    first = _reply_rows(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("UPDATE neurocomment_settings SET reply_wait_minutes = 90")
        connection.exec_driver_sql(
            "DELETE FROM schema_version WHERE version = ?",
            (_INBOX_VERSION,),
        )
    apply_migrations(engine)

    assert _reply_rows(engine) == first
    with engine.connect() as connection:
        count = connection.exec_driver_sql("SELECT COUNT(*) FROM schema_version").scalar_one()
    assert int(count) == len(MIGRATIONS)
