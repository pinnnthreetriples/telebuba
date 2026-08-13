"""Migration #52 — the comment-mode pair on ``neurocomment_settings``.

Its own file rather than another case in ``test_schema_migrations.py``: that one is
within a few lines of the 700-line test cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.db import _get_engine  # type: ignore[attr-defined]
from core.migration_steps_comment_mode import _add_neurocomment_settings_comment_mode

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

    from tests.core.conftest import _EngineFactory


def _settings_columns(connection: Connection) -> dict[str, dict[str, object]]:
    return {
        str(row["name"]): dict(row)
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(neurocomment_settings)",
        ).mappings()
    }


def test_migration_52_adds_the_comment_mode_columns() -> None:
    engine = _get_engine()
    with engine.connect() as connection:
        columns = _settings_columns(connection)
        versions = {
            int(row[0]) for row in connection.exec_driver_sql("SELECT version FROM schema_version")
        }
    # Both NOT NULL with the shipped defaults: this row is the operator's stored decision,
    # and a NULL mode would leave every reader inventing an answer nobody chose.
    assert columns["comment_mode"]["notnull"] == 1
    assert columns["reply_wait_minutes"]["notnull"] == 1
    # ``create_all`` quotes the server default and the ALTER does not, so compare the value.
    assert str(columns["comment_mode"]["dflt_value"]).strip("'") == "first"
    assert str(columns["reply_wait_minutes"]["dflt_value"]).strip("'") == "10"
    assert 52 in versions


def test_migration_52_alters_a_settings_row_that_predates_the_columns(
    legacy_engine: _EngineFactory,
) -> None:
    """The only path that ever runs on the operator's database, on a real legacy one.

    A stored override must come out of the upgrade commenting exactly as it did before:
    ``first``, with the wait it never chose left at the shipped default. The second call
    proves the PRAGMA guard, since a re-run ALTER would raise.
    """
    engine = legacy_engine("settings-pre-52.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE neurocomment_settings ("
            "  id INTEGER PRIMARY KEY CHECK (id = 1),"
            "  max_comments_per_hour INTEGER NOT NULL,"
            "  max_comments_per_channel_per_day INTEGER NOT NULL,"
            "  reply_delay_min_seconds REAL NOT NULL,"
            "  reply_delay_max_seconds REAL NOT NULL,"
            "  min_trust_score INTEGER NOT NULL,"
            "  updated_at VARCHAR NOT NULL"
            ")",
        )
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_settings VALUES (1, 4, 2, 1.0, 2.0, 30, 'then')",
        )
        assert "comment_mode" not in _settings_columns(connection)

        _add_neurocomment_settings_comment_mode(connection)
        _add_neurocomment_settings_comment_mode(connection)  # the "already there" branch.

    with engine.connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT comment_mode, reply_wait_minutes, min_trust_score FROM neurocomment_settings",
        ).one()
    assert row == ("first", 10, 30)


def test_migration_52_is_inert_without_the_table(legacy_engine: _EngineFactory) -> None:
    """A database that somehow never got #19 must not raise mid-upgrade."""
    engine = legacy_engine("settings-no-table.db")
    with engine.begin() as connection:
        _add_neurocomment_settings_comment_mode(connection)
