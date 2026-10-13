"""SQLAlchemy tables for persistent bulk-message runs (migration #91).

A run outlives a restart: ``bulk_runs`` holds what was asked for, one
``bulk_run_items`` row per planned send (a hand-over adds a row, the refused one
stays as history), and ``bulk_run_accounts`` each account's pacing state. No
foreign key to ``accounts``: a deleted account must not erase what was sent.

Mirrors ``core.migration_steps_bulk_runs``; ``tests/core/test_migrations_bulk_runs.py``
compares the two spellings.
"""

from __future__ import annotations

from sqlalchemy import Column, Index, Integer, String, Table, text

from core._schema_tables import _metadata

_bulk_runs = Table(
    "bulk_runs",
    _metadata,
    Column("id", String, primary_key=True),
    Column("owner_user_id", String, nullable=False),
    Column("status", String, nullable=False),
    # The resolved ``BulkMessageRequest``: every default already filled in, so a
    # resumed run keeps the pacing it started with even if the limits changed.
    Column("request_json", String, nullable=False),
    Column("cancel_requested", Integer, nullable=False, server_default=text("0")),
    # Why the run is idle and until when, for the progress view; NULL while sending.
    Column("next_send_at", String, nullable=True),
    Column("wait_reason", String, nullable=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("finished_at", String, nullable=True),
    Index("ix_bulk_runs_status", "status"),
    # At most one live run: two starts racing past the service's check meet here.
    Index(
        "ux_bulk_runs_live",
        "status",
        unique=True,
        sqlite_where=text("status = 'running'"),
    ),
)

_bulk_run_items = Table(
    "bulk_run_items",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("account_id", String, nullable=False),
    Column("recipient", String, nullable=False),
    Column("peer", String, nullable=False),
    # The zone the sending window was read in, and whether it is the client's own.
    Column("tz_name", String, nullable=False),
    Column("time_basis", String, nullable=False),
    Column("status", String, nullable=False),
    Column("error_code", String, nullable=True),
    Column("retry_after_seconds", Integer, nullable=True),
    Column("handed_over", Integer, nullable=False, server_default=text("0")),
    # Completion order for the results list; NULL until the item is final.
    Column("finished_seq", Integer, nullable=True),
    Column("finished_at", String, nullable=True),
    Index("ix_bulk_run_items_run", "run_id", "status"),
)

_bulk_run_accounts = Table(
    "bulk_run_accounts",
    _metadata,
    Column("run_id", String, primary_key=True),
    Column("account_id", String, primary_key=True),
    # Set once the account cannot send for the rest of the run.
    Column("stop_code", String, nullable=True),
    Column("retry_after_seconds", Integer, nullable=True),
    # No send before this moment (its pause or series break).
    Column("ready_at", String, nullable=True),
    Column("series_count", Integer, nullable=False, server_default=text("0")),
)
