"""Tests for the SQLite migration registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core._schema_tables import _warming_account_state  # type: ignore[attr-defined]
from core.db import _get_engine, configure_database  # type: ignore[attr-defined]
from core.migration_steps import _add_users_token_version
from core.migration_steps_discovery import (
    _add_neurocomment_discovery_candidates,
    _add_warming_settings_telemetr_key,
)
from core.migration_steps_pool import _add_proxy_geo_consensus
from core.migrations import MIGRATIONS, _rename_proxy_type_http_to_https, apply_migrations

if TYPE_CHECKING:
    from pathlib import Path

    from tests.core.conftest import _EngineFactory


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


def test_legacy_database_is_brought_up_to_date(legacy_engine: _EngineFactory) -> None:
    """An old DB missing ``schema_version`` gets stamped, not re-altered."""
    # Simulate an old database: create just the accounts table without
    # `bio`, and no schema_version table. apply_migrations should stamp it
    # and add the missing column.
    engine = legacy_engine("legacy.db")
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
            "username VARCHAR,"
            "password VARCHAR,"
            "status VARCHAR NOT NULL,"
            "last_checked_at VARCHAR,"
            "last_error VARCHAR,"
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
    assert [int(row[0]) for row in applied] == [v for v, _n, _f in MIGRATIONS]
    assert any(row["name"] == "bio" for row in bio_present)


def test_rename_proxy_type_http_to_https_migrates_existing_rows(
    legacy_engine: _EngineFactory,
) -> None:
    """Pre-existing rows stored as proxy_type='http' must surface as 'https'.

    ``account_proxies`` was retired by migration #18, so this exercises the #9
    body directly against a hand-built legacy table.
    """
    engine = legacy_engine("legacy.db")
    now = "2026-01-01T00:00:00+00:00"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE account_proxies ("
            "account_id VARCHAR PRIMARY KEY, proxy_type VARCHAR NOT NULL, host VARCHAR NOT NULL, "
            "port INTEGER NOT NULL, status VARCHAR NOT NULL, created_at VARCHAR NOT NULL, "
            "updated_at VARCHAR NOT NULL)",
        )
        connection.exec_driver_sql(
            "INSERT INTO account_proxies "
            "(account_id, proxy_type, host, port, status, created_at, updated_at) "
            "VALUES ('acc-legacy', 'http', '1.2.3.4', 8080, 'unknown', ?, ?)",
            (now, now),
        )
        _rename_proxy_type_http_to_https(connection)

    with engine.connect() as connection:
        proxy_type = connection.exec_driver_sql(
            "SELECT proxy_type FROM account_proxies WHERE account_id = 'acc-legacy'",
        ).scalar()
    assert proxy_type == "https"


def test_proxy_pool_migration_collapses_and_drops_account_proxies(
    legacy_engine: _EngineFactory,
) -> None:
    """#18: two accounts on one endpoint collapse to a single pool proxy; old table dropped."""
    engine = legacy_engine("legacy.db")
    now = "2026-01-01T00:00:00+00:00"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE accounts ("
            "account_id VARCHAR PRIMARY KEY, session_name VARCHAR, status VARCHAR NOT NULL, "
            "created_at VARCHAR NOT NULL, updated_at VARCHAR NOT NULL)",
        )
        connection.exec_driver_sql(
            "CREATE TABLE account_proxies ("
            "account_id VARCHAR PRIMARY KEY, proxy_type VARCHAR NOT NULL, host VARCHAR NOT NULL, "
            "port INTEGER NOT NULL, username VARCHAR, password VARCHAR, status VARCHAR NOT NULL, "
            "last_checked_at VARCHAR, last_error VARCHAR, "
            "created_at VARCHAR NOT NULL, updated_at VARCHAR NOT NULL)",
        )
        connection.exec_driver_sql(
            "CREATE TABLE warming_account_state ("
            "account_id VARCHAR PRIMARY KEY, state VARCHAR NOT NULL, "
            "cycles_completed INTEGER NOT NULL, updated_at VARCHAR NOT NULL)",
        )
        connection.exec_driver_sql(
            "CREATE TABLE warming_settings ("
            "id INTEGER PRIMARY KEY, inter_account_chat INTEGER NOT NULL, "
            "reactions_enabled INTEGER NOT NULL, gemini_api_key VARCHAR NOT NULL, "
            "gemini_model VARCHAR NOT NULL, updated_at VARCHAR NOT NULL)",
        )
        for account_id in ("acc-a", "acc-b"):
            connection.exec_driver_sql(
                "INSERT INTO accounts (account_id, status, created_at, updated_at) "
                "VALUES (?, 'new', ?, ?)",
                (account_id, now, now),
            )
            connection.exec_driver_sql(
                "INSERT INTO account_proxies "
                "(account_id, proxy_type, host, port, status, created_at, updated_at) "
                "VALUES (?, 'socks5', 'shared.example', 1080, 'tcp_working', ?, ?)",
                (account_id, now, now),
            )

    apply_migrations(engine)

    with engine.connect() as connection:
        proxies = connection.exec_driver_sql("SELECT id FROM proxies").all()
        assigned = connection.exec_driver_sql(
            "SELECT DISTINCT proxy_id FROM accounts WHERE proxy_id IS NOT NULL",
        ).all()
        table_gone = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'account_proxies'",
        ).first()

    assert len(proxies) == 1  # both accounts shared one endpoint → one pool proxy
    assert len(assigned) == 1  # both point at it
    assert table_gone is None  # account_proxies dropped


def test_token_version_migration_adds_column_defaulting_to_zero(
    legacy_engine: _EngineFactory,
) -> None:
    """#22: a legacy ``users`` table (no token_version) gains it, defaulting to 0."""
    engine = legacy_engine("legacy.db")
    now = "2026-01-01T00:00:00+00:00"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE users ("
            "id VARCHAR PRIMARY KEY, username VARCHAR NOT NULL UNIQUE, "
            "password_hash VARCHAR NOT NULL, role VARCHAR NOT NULL, "
            "created_at VARCHAR NOT NULL, updated_at VARCHAR NOT NULL)",
        )
        connection.exec_driver_sql(
            "INSERT INTO users (id, username, password_hash, role, created_at, updated_at) "
            "VALUES ('u-legacy', 'old', 'hash', 'admin', ?, ?)",
            (now, now),
        )
        _add_users_token_version(connection)
    with engine.connect() as connection:
        version = connection.exec_driver_sql(
            "SELECT token_version FROM users WHERE id = 'u-legacy'",
        ).scalar()
    assert int(version) == 0


def test_logs_indexes_created_and_migration_idempotent() -> None:
    """Audit #2: #23 creates both logs indexes; re-running the registry is clean."""
    engine = _get_engine()
    apply_migrations(engine)  # second pass must be a no-op, not raise.
    with engine.connect() as connection:
        index_names = {
            str(row["name"])
            for row in connection.exec_driver_sql("PRAGMA index_list(logs)").mappings()
        }
        versions = {
            int(row[0]) for row in connection.exec_driver_sql("SELECT version FROM schema_version")
        }
        version_count = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM schema_version",
        ).scalar_one()
    assert {"ix_logs_account_id", "ix_logs_created_at"} <= index_names
    assert 23 in versions
    assert int(version_count) == len(MIGRATIONS)  # no duplicate stamping


def test_logs_indexes_added_to_legacy_logs_table(legacy_engine: _EngineFactory) -> None:
    """A legacy logs table with only its PK gains both indexes on migrate."""
    from core.migration_steps import _add_logs_indexes  # noqa: PLC0415

    engine = legacy_engine("legacy.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE logs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, created_at VARCHAR NOT NULL, "
            "level VARCHAR NOT NULL, status VARCHAR NOT NULL, account_id VARCHAR, "
            "event VARCHAR NOT NULL, extra VARCHAR NOT NULL)",
        )
        _add_logs_indexes(connection)
        # Idempotent at the body level too (IF NOT EXISTS).
        _add_logs_indexes(connection)
    with engine.connect() as connection:
        index_names = {
            str(row["name"])
            for row in connection.exec_driver_sql("PRAGMA index_list(logs)").mappings()
        }
    assert {"ix_logs_account_id", "ix_logs_created_at"} <= index_names


def test_campaign_account_channel_column_created_and_defaults_null() -> None:
    """#25 adds the nullable ``channel`` pin column; a fresh assignment defaults NULL."""
    engine = _get_engine()
    with engine.connect() as connection:
        columns = {
            str(row["name"])
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(neurocomment_campaign_accounts)",
            ).mappings()
        }
        versions = {
            int(row[0]) for row in connection.exec_driver_sql("SELECT version FROM schema_version")
        }
    assert "channel" in columns
    assert 25 in versions


def test_campaign_account_channel_migration_idempotent_on_legacy_table(
    legacy_engine: _EngineFactory,
) -> None:
    """A legacy account-link table (no ``channel``) gains it, defaulting NULL; re-run is clean."""
    from core.migration_steps_neurocomment import _add_campaign_account_channel  # noqa: PLC0415

    engine = legacy_engine("legacy.db")
    now = "2026-01-01T00:00:00+00:00"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE neurocomment_campaign_accounts ("
            "campaign_id VARCHAR NOT NULL, account_id VARCHAR NOT NULL, "
            "created_at VARCHAR NOT NULL, PRIMARY KEY (campaign_id, account_id))",
        )
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_campaign_accounts (campaign_id, account_id, created_at) "
            "VALUES ('c1', 'acc-1', ?)",
            (now,),
        )
        _add_campaign_account_channel(connection)
        _add_campaign_account_channel(connection)  # idempotent — must not raise/duplicate.
    with engine.connect() as connection:
        channel = connection.exec_driver_sql(
            "SELECT channel FROM neurocomment_campaign_accounts WHERE account_id = 'acc-1'",
        ).scalar()
    assert channel is None


def test_campaign_account_channel_migration_skips_missing_table(
    legacy_engine: _EngineFactory,
) -> None:
    """The #25 body is a no-op when the account-link table does not exist yet."""
    from core.migration_steps_neurocomment import _add_campaign_account_channel  # noqa: PLC0415

    engine = legacy_engine("empty.db")
    with engine.begin() as connection:
        _add_campaign_account_channel(connection)  # no table → returns, no raise.


def test_campaign_account_channels_table_created() -> None:
    """#29 adds the channel-subset join table and stamps its version."""
    engine = _get_engine()
    with engine.connect() as connection:
        tables = {
            str(row[0])
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'",
            )
        }
        versions = {
            int(row[0]) for row in connection.exec_driver_sql("SELECT version FROM schema_version")
        }
    assert "neurocomment_campaign_account_channels" in tables
    assert 29 in versions


def test_campaign_account_channels_migration_backfills_legacy_pins(
    legacy_engine: _EngineFactory,
) -> None:
    """A legacy single ``channel`` pin is backfilled as one subset row; re-run is clean."""
    from core.migration_steps_neurocomment import (  # noqa: PLC0415
        _add_campaign_account_channels_table,
    )

    engine = legacy_engine("legacy.db")
    now = "2026-01-01T00:00:00+00:00"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE neurocomment_campaign_accounts ("
            "campaign_id VARCHAR NOT NULL, account_id VARCHAR NOT NULL, "
            "created_at VARCHAR NOT NULL, channel VARCHAR, "
            "PRIMARY KEY (campaign_id, account_id))",
        )
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_campaign_accounts "
            "(campaign_id, account_id, created_at, channel) VALUES "
            "('c1', 'pinned', ?, '@news'), ('c1', 'free', ?, NULL)",
            (now, now),
        )
        _add_campaign_account_channels_table(connection)
        _add_campaign_account_channels_table(connection)  # idempotent — no dupe rows.
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT account_id, channel FROM neurocomment_campaign_account_channels",
        ).all()
    assert rows == [("pinned", "@news")]  # only the non-NULL pin is backfilled


def test_neurocomment_cooldowns_table_created() -> None:
    """#34 adds the durable cooldown table and stamps its version."""
    engine = _get_engine()
    with engine.connect() as connection:
        tables = {
            str(row[0])
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'",
            )
        }
        versions = {
            int(row[0]) for row in connection.exec_driver_sql("SELECT version FROM schema_version")
        }
    assert "neurocomment_cooldowns" in tables
    assert 34 in versions


def test_neurocomment_cooldowns_migration_is_idempotent(legacy_engine: _EngineFactory) -> None:
    """The #34 body uses IF NOT EXISTS, so re-running it against a legacy DB is clean."""
    from core.migration_steps_neurocomment import _add_neurocomment_cooldowns  # noqa: PLC0415

    engine = legacy_engine("legacy.db")
    with engine.begin() as connection:
        _add_neurocomment_cooldowns(connection)
        _add_neurocomment_cooldowns(connection)  # idempotent — must not raise.
    with engine.connect() as connection:
        columns = {
            str(row["name"])
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(neurocomment_cooldowns)",
            ).mappings()
        }
    assert {"account_id", "channel", "until"} == columns


def test_campaign_channel_pause_columns_added_and_default_to_unpaused(
    legacy_engine: _EngineFactory,
) -> None:
    """#42: a legacy channel-link table gains the round counter + pause deadline.

    Existing links must read as "never paused, zero rounds" — the four-round rule starts
    everyone from a clean slate rather than inheriting whatever the in-memory back-off
    happened to hold at upgrade time.
    """
    from core.migration_steps_channel_pause import _add_campaign_channel_pause  # noqa: PLC0415

    engine = legacy_engine("legacy-pause.db")
    now = "2026-01-01T00:00:00+00:00"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE neurocomment_campaign_channels ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id VARCHAR NOT NULL, "
            "channel VARCHAR NOT NULL, active INTEGER NOT NULL, created_at VARCHAR NOT NULL)",
        )
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_campaign_channels "
            "(campaign_id, channel, active, created_at) VALUES ('c1', '@news', 1, ?)",
            (now,),
        )
        _add_campaign_channel_pause(connection)
        _add_campaign_channel_pause(connection)  # idempotent — must not raise.

    with engine.connect() as connection:
        rounds, until = connection.exec_driver_sql(
            "SELECT pause_rounds, paused_until FROM neurocomment_campaign_channels",
        ).one()
    assert (int(rounds), until) == (0, None)


def test_campaign_channel_pause_migration_skips_missing_table(
    legacy_engine: _EngineFactory,
) -> None:
    """The #42 body is a no-op when the channel-link table does not exist yet."""
    from core.migration_steps_channel_pause import _add_campaign_channel_pause  # noqa: PLC0415

    engine = legacy_engine("empty-pause.db")
    with engine.begin() as connection:
        _add_campaign_channel_pause(connection)  # no table → returns, no raise.


def test_readiness_rejoin_columns_added_and_default_to_never_retried(
    legacy_engine: _EngineFactory,
) -> None:
    """#43: a legacy readiness table gains the re-join counter + its last attempt.

    Existing rows must read as "never retried", so the first sweep after the upgrade gives
    every parked pair its four attempts rather than inheriting somebody else's counter.
    """
    from core.migration_steps_rejoin import _add_readiness_rejoin  # noqa: PLC0415

    engine = legacy_engine("legacy-rejoin.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE neurocomment_readiness ("
            "account_id VARCHAR NOT NULL, channel VARCHAR NOT NULL, joined INTEGER NOT NULL, "
            "captcha_passed INTEGER NOT NULL, ready INTEGER NOT NULL, "
            "checked_at VARCHAR NOT NULL, PRIMARY KEY (account_id, channel))",
        )
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_readiness VALUES ('a1', '@news', 0, 1, 0, '2026-01-01')",
        )
        _add_readiness_rejoin(connection)
        _add_readiness_rejoin(connection)  # idempotent — must not raise.

    with engine.connect() as connection:
        attempts, attempted_at = connection.exec_driver_sql(
            "SELECT rejoin_attempts, rejoin_attempted_at FROM neurocomment_readiness",
        ).one()
    assert (int(attempts), attempted_at) == (0, None)


def test_readiness_access_lost_reason_added_and_defaults_to_unknown(
    legacy_engine: _EngineFactory,
) -> None:
    """#44: a legacy readiness table gains the verdict that parked each pair.

    NULL on every existing row, and NULL must read as "unknown" — the re-join rule treats
    it exactly as it treated every row before the column existed. A default that read as a
    terminal verdict would unlink live channels on the first sweep after the upgrade.
    """
    from core.migration_steps_access_reason import (  # noqa: PLC0415
        _add_readiness_access_lost_reason,
    )

    engine = legacy_engine("legacy-access-reason.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE neurocomment_readiness ("
            "account_id VARCHAR NOT NULL, channel VARCHAR NOT NULL, joined INTEGER NOT NULL, "
            "captcha_passed INTEGER NOT NULL, ready INTEGER NOT NULL, "
            "checked_at VARCHAR NOT NULL, PRIMARY KEY (account_id, channel))",
        )
        connection.exec_driver_sql(
            "INSERT INTO neurocomment_readiness VALUES ('a1', '@news', 0, 1, 0, '2026-01-01')",
        )
        _add_readiness_access_lost_reason(connection)
        _add_readiness_access_lost_reason(connection)  # idempotent — must not raise.

    with engine.connect() as connection:
        reason = connection.exec_driver_sql(
            "SELECT access_lost_reason FROM neurocomment_readiness",
        ).scalar_one()
    assert reason is None


def test_append_only_versions_are_unique() -> None:
    """Two migrations sharing the same version would silently mask each other."""
    versions = [v for v, _name, _fn in MIGRATIONS]
    assert len(versions) == len(set(versions))
    # Should also be a strictly increasing sequence — catches an off-by-one.
    assert versions == sorted(versions)


def test_proxy_geo_consensus_migration_is_idempotent(legacy_engine: _EngineFactory) -> None:
    """#33 adds provider-specific country results and an unknown status default."""
    engine = legacy_engine("legacy-proxy.db")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE proxies (id VARCHAR PRIMARY KEY)")
        _add_proxy_geo_consensus(connection)
        _add_proxy_geo_consensus(connection)
        connection.exec_driver_sql("INSERT INTO proxies (id) VALUES ('proxy-1')")

    with engine.connect() as connection:
        columns = {
            str(row["name"])
            for row in connection.exec_driver_sql("PRAGMA table_info(proxies)").mappings()
        }
        status = connection.exec_driver_sql(
            "SELECT geo_status FROM proxies WHERE id = 'proxy-1'",
        ).scalar_one()

    assert {"geo_status", "ipinfo_country_code", "maxmind_country_code"} <= columns
    assert status == "unknown"


def test_telemetr_key_migration_adds_the_column_and_is_idempotent(
    legacy_engine: _EngineFactory,
) -> None:
    """#37 puts the Telemetr.io discovery key on the shared provider-key row."""
    engine = legacy_engine("legacy-telemetr.db")
    with engine.begin() as connection:
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
        _add_warming_settings_telemetr_key(connection)
        _add_warming_settings_telemetr_key(connection)

    with engine.connect() as connection:
        columns = {
            str(row["name"])
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(warming_settings)",
            ).mappings()
        }
    assert "telemetr_api_key" in columns


def test_telemetr_key_migration_skips_a_db_without_the_settings_table(
    legacy_engine: _EngineFactory,
) -> None:
    """A hand-built legacy DB without ``warming_settings`` is a no-op, not an error."""
    engine = legacy_engine("no-settings.db")
    with engine.begin() as connection:
        _add_warming_settings_telemetr_key(connection)


def test_discovery_candidates_migration_creates_table_and_index(
    legacy_engine: _EngineFactory,
) -> None:
    """#38 adds the per-campaign discovery scratch set plus its progress index."""
    engine = legacy_engine("legacy-discovery.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE neurocomment_campaigns (campaign_id VARCHAR PRIMARY KEY)",
        )
        _add_neurocomment_discovery_candidates(connection)
        _add_neurocomment_discovery_candidates(connection)

    with engine.connect() as connection:
        columns = {
            str(row["name"])
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(neurocomment_discovery_candidates)",
            ).mappings()
        }
        indexes = {
            str(row["name"])
            for row in connection.exec_driver_sql(
                "PRAGMA index_list(neurocomment_discovery_candidates)",
            ).mappings()
        }

    assert {
        "campaign_id",
        "channel",
        "title",
        "subscribers",
        "source",
        "qualified_at",
        "qualify_error",
        "created_at",
    } == columns
    assert "ix_nc_discovery_campaign_qualified" in indexes


def test_reservation_token_upgrade_matches_a_fresh_database(
    legacy_engine: _EngineFactory,
) -> None:
    """#46: an upgraded ``warming_account_state`` ends in the same shape as a fresh one.

    The reservation hand-back's guard is this column (#10), so a row that predates the
    migration must gain it — NULL, which matches no token, i.e. fail-closed — rather
    than leave the guard comparing against a column that is not there.
    """
    engine = legacy_engine("legacy.db")
    now = "2026-01-01T00:00:00+00:00"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE accounts ("
            "account_id VARCHAR PRIMARY KEY, session_name VARCHAR, status VARCHAR NOT NULL, "
            "created_at VARCHAR NOT NULL, updated_at VARCHAR NOT NULL)",
        )
        connection.exec_driver_sql(
            "CREATE TABLE warming_account_state ("
            "account_id VARCHAR PRIMARY KEY, state VARCHAR NOT NULL, "
            "cycles_completed INTEGER NOT NULL, updated_at VARCHAR NOT NULL)",
        )
        connection.exec_driver_sql(
            "CREATE TABLE warming_settings ("
            "id INTEGER PRIMARY KEY, inter_account_chat INTEGER NOT NULL, "
            "reactions_enabled INTEGER NOT NULL, gemini_api_key VARCHAR NOT NULL, "
            "gemini_model VARCHAR NOT NULL, updated_at VARCHAR NOT NULL)",
        )
        connection.exec_driver_sql(
            "INSERT INTO warming_account_state VALUES ('acc-1', 'sleeping', 3, ?)",
            (now,),
        )

    apply_migrations(engine)
    apply_migrations(engine)  # idempotent: the ADD COLUMN guard must hold on a rerun

    with engine.connect() as connection:
        upgraded = {
            str(row["name"])
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(warming_account_state)",
            ).mappings()
        }
        carried = connection.exec_driver_sql(
            "SELECT reservation_token FROM warming_account_state WHERE account_id = 'acc-1'",
        ).scalar_one()
    # The fresh path: ``_isolate_db`` already built one from ``create_all`` + the whole
    # registry. Both routes must land on the same column, or the guard is comparing
    # against something only one kind of database has.
    with _get_engine().connect() as connection:
        built = {
            str(row["name"])
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(warming_account_state)",
            ).mappings()
        }

    assert "reservation_token" in upgraded
    assert "reservation_token" in built
    assert {column.name for column in _warming_account_state.columns} <= built
    # NULL matches no token, so a booking in flight across the upgrade fails closed.
    assert carried is None
