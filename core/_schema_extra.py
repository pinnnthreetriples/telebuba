"""Tables that live outside ``core._schema_tables`` for its file-size budget.

Importing this module registers each of them on the shared ``_metadata``; ``core.db``
imports it once so ``create_all`` and the migrations see every table.
"""

from __future__ import annotations

from core import (
    _schema_bulk_runs,
    _schema_character,
    _schema_lifecycle,
    _schema_notifications,
    _schema_outreach,
)

__all__ = [
    "_schema_bulk_runs",
    "_schema_character",
    "_schema_lifecycle",
    "_schema_notifications",
    "_schema_outreach",
]
