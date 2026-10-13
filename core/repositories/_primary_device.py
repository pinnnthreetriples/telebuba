"""SQL for "the account's primary device": desktop when it has one, else mobile.

Every read that shows ONE fingerprint per account (the accounts table, the warming
board, trust, the neurocomment engine) joins on this, so a second authorization
never duplicates an account row or swaps which fingerprint a page shows. Mirrors
``schemas.device_fingerprint.account_devices`` — primary first — in SQL.

Imports only the table objects, like ``_device_fingerprint_language``, so any
repository module can use it without reaching ``core.db``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import and_, case

from core._schema_tables import _accounts, _device_fingerprints

if TYPE_CHECKING:
    from sqlalchemy import ColumnElement


def primary_device_type() -> ColumnElement[str]:
    """``'mobile'`` only for a mobile-only account; every other account is desktop."""
    return case(
        (
            and_(
                _accounts.c.session_device == "mobile",
                _accounts.c.second_session_name.is_(None),
            ),
            "mobile",
        ),
        else_="desktop",
    )


def joins_primary_fingerprint() -> ColumnElement[bool]:
    """The join condition from ``accounts`` to its primary-device fingerprint row."""
    return and_(
        _accounts.c.account_id == _device_fingerprints.c.account_id,
        _device_fingerprints.c.device_type == primary_device_type(),
    )
