"""Schema migration #74 — dual device fingerprints (Desktop + Mobile) and warming device_mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def migrate_dual_device_fingerprints(conn: Connection) -> None:
    """Upgrade ``device_fingerprints`` to composite PK ``(account_id, device_type)``.

    Also adds ``device_mode`` (default ``'mobile'``) to ``warming_settings`` and
    ``warming_account_state`` when absent. Idempotent on both fresh and migrated DBs.
    """
    info = conn.execute(text("PRAGMA table_info(device_fingerprints)")).fetchall()
    if info:
        col_names = {str(row[1]) for row in info}
        pk_cols = {str(row[1]) for row in info if int(row[5]) > 0}
        if pk_cols != {"account_id", "device_type"}:
            conn.execute(text("DROP TABLE IF EXISTS device_fingerprints_new"))
            conn.execute(
                text(
                    """
                    CREATE TABLE device_fingerprints_new (
                        account_id TEXT NOT NULL,
                        device_type TEXT NOT NULL DEFAULT 'desktop',
                        platform TEXT NOT NULL,
                        device_model TEXT NOT NULL,
                        system_version TEXT NOT NULL,
                        app_version TEXT NOT NULL,
                        lang_code TEXT NOT NULL,
                        system_lang_code TEXT NOT NULL,
                        PRIMARY KEY (account_id, device_type)
                    )
                    """
                )
            )
            if "device_type" in col_names:
                conn.execute(
                    text(
                        """
                        INSERT INTO device_fingerprints_new (
                            account_id, device_type, platform, device_model,
                            system_version, app_version, lang_code, system_lang_code
                        )
                        SELECT
                            account_id, COALESCE(device_type, 'desktop'), platform, device_model,
                            system_version, app_version, lang_code, system_lang_code
                        FROM device_fingerprints
                        """
                    )
                )
            else:
                conn.execute(
                    text(
                        """
                        INSERT INTO device_fingerprints_new (
                            account_id, device_type, platform, device_model,
                            system_version, app_version, lang_code, system_lang_code
                        )
                        SELECT
                            account_id, 'desktop', platform, device_model,
                            system_version, app_version, lang_code, system_lang_code
                        FROM device_fingerprints
                        """
                    )
                )
            conn.execute(text("DROP TABLE device_fingerprints"))
            conn.execute(text("ALTER TABLE device_fingerprints_new RENAME TO device_fingerprints"))
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_device_fingerprints_account_type "
                "ON device_fingerprints(account_id, device_type)"
            )
        )

    ws_info = conn.execute(text("PRAGMA table_info(warming_settings)")).fetchall()
    if ws_info and "device_mode" not in {str(row[1]) for row in ws_info}:
        conn.execute(
            text(
                "ALTER TABLE warming_settings ADD COLUMN device_mode TEXT NOT NULL DEFAULT 'mobile'"
            )
        )

    was_info = conn.execute(text("PRAGMA table_info(warming_account_state)")).fetchall()
    if was_info and "device_mode" not in {str(row[1]) for row in was_info}:
        conn.execute(
            text(
                "ALTER TABLE warming_account_state "
                "ADD COLUMN device_mode TEXT NOT NULL DEFAULT 'mobile'"
            )
        )
