"""Migration 120 gives back the @SpamBot answers a passive-decision mirror overwrote (audit F-006).

Only a row whose ``(status, detail)`` is exactly what the mirror wrote is touched, and only when
the lifecycle journal holds the bot's real answer to put back; everything else stays.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.db import _metadata
from core.migration_steps_spam_mirror_repair import _add_spam_mirror_repair
from core.migrations import MIGRATIONS

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Connection, Engine

    _EngineFactory = Callable[[str], Engine]

_MIRROR_AT = "2026-11-17T12:00:00+00:00"
_BOT_AT = "2026-11-10T08:00:00+00:00"


def _status(connection: Connection, account: str) -> tuple[object, ...]:
    row = connection.exec_driver_sql(
        "SELECT status, detail, checked_at, fold_status FROM account_spam_status"
        " WHERE account_id = ?",
        (account,),
    ).one()
    return tuple(row)


def _seed(engine: Engine) -> None:
    rows = [
        # account, status, detail, checked_at, in that order
        ("mirrored", "limited", "peer_flood_send", _MIRROR_AT),
        ("frozen", "unknown", "frozen", _MIRROR_AT),
        ("expired", "unknown", "limit_expired", _MIRROR_AT),
        ("no_journal", "limited", "user_banned_in_channel", _MIRROR_AT),
        ("bot_limit", "limited", "until 29 Nov 2026", _MIRROR_AT),  # a real answer
        ("masstag", "limited", "peer_flood", _MIRROR_AT),  # masstagging's own write
        ("projection_only", "unknown", None, ""),
    ]
    with engine.begin() as connection:
        for account, status, detail, checked in rows:
            connection.exec_driver_sql(
                "INSERT INTO account_spam_status (account_id, status, detail, checked_at,"
                " fold_status) VALUES (?, ?, ?, ?, 'limited')",
                (account, status, detail, checked),
            )
        connection.exec_driver_sql(
            "INSERT INTO account_lifecycle_events (id, account_id, kind, created_at, extra)"
            " VALUES (1, 'mirrored', 'spambot_verdict', '2026-10-01T00:00:00+00:00',"
            ' \'{"status": "limited", "detail": null}\')'
        )
        for account, extra in (
            ("mirrored", '{"status": "clean", "detail": null}'),
            ("frozen", '{"status": "limited", "detail": "until 29 Nov 2026"}'),
            ("expired", '{"status": "limited", "detail": "until 01 Nov 2026"}'),
            ("bot_limit", '{"status": "clean", "detail": null}'),
            ("masstag", '{"status": "clean", "detail": null}'),
        ):
            connection.exec_driver_sql(
                "INSERT INTO account_lifecycle_events (account_id, kind, created_at, extra)"
                " VALUES (?, 'spambot_verdict', ?, ?)",
                (account, _BOT_AT, extra),
            )
        # A broken row must not be restored from.
        connection.exec_driver_sql(
            "INSERT INTO account_lifecycle_events (account_id, kind, created_at, extra)"
            " VALUES ('no_journal', 'spambot_verdict', ?, 'not json')",
            (_BOT_AT,),
        )


def test_the_registry_carries_it_once_after_119() -> None:
    entries = [e for e in MIGRATIONS if e[2] is _add_spam_mirror_repair]
    assert entries == [(120, "repair_spam_status_mirror", _add_spam_mirror_repair)]


def test_a_mirrored_row_gets_the_bots_last_answer_back_and_nothing_else_moves(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("repair.db")
    _metadata.create_all(engine)
    _seed(engine)

    for _ in range(2):  # idempotent
        with engine.begin() as connection:
            _add_spam_mirror_repair(connection)

    with engine.connect() as connection:
        # Restored from the newest readable journal row, projection column untouched.
        assert _status(connection, "mirrored") == ("clean", None, _BOT_AT, "limited")
        assert _status(connection, "frozen") == ("limited", "until 29 Nov 2026", _BOT_AT, "limited")
        assert _status(connection, "expired") == (
            "limited",
            "until 01 Nov 2026",
            _BOT_AT,
            "limited",
        )
        # No journal entry to restore from (or only an unreadable one): left as it is.
        assert _status(connection, "no_journal")[:3] == (
            "limited",
            "user_banned_in_channel",
            _MIRROR_AT,
        )
        # A real answer, masstagging's explicit write and a projection-only row are never touched.
        assert _status(connection, "bot_limit")[:3] == ("limited", "until 29 Nov 2026", _MIRROR_AT)
        assert _status(connection, "masstag")[:3] == ("limited", "peer_flood", _MIRROR_AT)
        assert _status(connection, "projection_only")[:3] == ("unknown", None, "")


def test_a_database_without_the_tables_is_left_alone(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("bare.db")
    with engine.begin() as connection:
        _add_spam_mirror_repair(connection)
