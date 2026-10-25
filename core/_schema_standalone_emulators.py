"""SQLAlchemy table for the independent emulators: phones that carry no account.

Its own module because ``core._schema_tables`` is near the file-size budget. Mirrors
``core.migration_steps_standalone_emulators``: ``create_all`` builds a fresh database
from THIS module while an existing one gets migrations 101 and 102, and
``tests/core/test_migrations_standalone_emulators.py`` compares the two.

The phone's Android identity is the same five fields a mobile account's
``device_fingerprints`` row holds; the row's ``id`` seeds its serial and region.
"""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, String, Table, text

from core._schema_tables import _metadata

_standalone_emulators = Table(
    "standalone_emulators",
    _metadata,
    Column("id", String, primary_key=True),
    Column("label", String, nullable=True),
    # One pool proxy, counted into its capacity like an account's; SET NULL on the
    # proxy's delete is done in the repo, as for ``accounts.proxy_id``.
    Column("proxy_id", String, ForeignKey("proxies.id"), nullable=True),
    # The apps its start installs when missing, comma-joined ``StandaloneApp`` names.
    Column("install_apps", String, nullable=False, server_default=text("'telegram'")),
    Column("device_model", String, nullable=False),
    Column("system_version", String, nullable=False),
    Column("app_version", String, nullable=False),
    Column("lang_code", String, nullable=False),
    Column("system_lang_code", String, nullable=False),
    Column("created_at", String, nullable=False),
)
