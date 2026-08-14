"""Proxy-host integrity migration.

The API and repository reject blank hosts, while these database triggers keep
the same invariant for legacy scripts and future internal write paths. Rows
written before either guard existed are swept here: blank hosts are deleted
(nothing can repair them) and the survivors are folded onto the canonical
identity ``ProxyCreate`` now produces. Both sweeps report what they touched —
a migration that silently changes operator data is a migration the operator
cannot audit.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists
from schemas.proxy import canonicalize_proxy_host

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)


def _harden_proxy_hosts(connection: Connection) -> None:
    """Remove unusable legacy rows, canonicalize the rest, enforce non-blank hosts."""
    if not _sqlite_table_exists(connection, "proxies"):
        return
    if "host" not in _sqlite_columns(connection, "proxies"):
        return

    _delete_blank_hosts(connection)
    _canonicalize_surviving_hosts(connection)
    connection.exec_driver_sql(
        "CREATE TRIGGER IF NOT EXISTS proxies_host_non_blank_insert "
        "BEFORE INSERT ON proxies "
        "WHEN length(trim(NEW.host)) = 0 "
        "BEGIN SELECT RAISE(ABORT, 'proxy host must not be blank'); END",
    )
    connection.exec_driver_sql(
        "CREATE TRIGGER IF NOT EXISTS proxies_host_non_blank_update "
        "BEFORE UPDATE OF host ON proxies "
        "WHEN length(trim(NEW.host)) = 0 "
        "BEGIN SELECT RAISE(ABORT, 'proxy host must not be blank'); END",
    )


def _delete_blank_hosts(connection: Connection) -> None:
    """Drop rows no API call can repair, naming every one of them for the operator.

    A blank host makes ``GET /proxies`` fail for the whole pool, and neither the
    endpoint nor the repository will accept an edit that fixes it — deletion is the
    only way out. The operator still has to re-add those endpoints and re-assign the
    accounts by hand, so the log is the only record they get.
    """
    deleted = [
        str(row[0])
        for row in connection.exec_driver_sql(
            "SELECT id FROM proxies WHERE length(trim(host)) = 0 ORDER BY id",
        ).all()
    ]
    if not deleted:
        return
    detached: list[str] = []
    if _sqlite_table_exists(connection, "accounts") and "proxy_id" in _sqlite_columns(
        connection,
        "accounts",
    ):
        detached = [
            str(row[0])
            for row in connection.exec_driver_sql(
                "SELECT account_id FROM accounts "
                "WHERE proxy_id IN (SELECT id FROM proxies WHERE length(trim(host)) = 0) "
                "ORDER BY account_id",
            ).all()
        ]
        # Detach before the delete so no account is left pointing at a missing row.
        connection.exec_driver_sql(
            "UPDATE accounts SET proxy_id = NULL "
            "WHERE proxy_id IN (SELECT id FROM proxies WHERE length(trim(host)) = 0)",
        )
    connection.exec_driver_sql("DELETE FROM proxies WHERE length(trim(host)) = 0")
    logger.warning(
        "migration 54: deleted %s proxy row(s) with a blank host — such a row breaks the whole "
        "pool listing and cannot be edited back into shape through the API, so re-add those "
        "endpoints by hand. Deleted proxy ids: %s. Accounts detached from them (re-assign a "
        "proxy to each): %s",
        len(deleted),
        ", ".join(deleted),
        ", ".join(detached) or "none",
    )


def _canonicalize_surviving_hosts(connection: Connection) -> None:
    """Fold stored hosts onto the identity ``ProxyCreate`` produces from now on.

    Identity is ``(host, port, proxy_type)``. A row stored as ``PROXY.Example.COM``
    would never match the canonical ``proxy.example.com`` the API now sends, so
    re-adding that endpoint would mint a second pool card instead of refreshing the
    credentials of the first.
    """
    if not {"port", "proxy_type"} <= _sqlite_columns(connection, "proxies"):
        return  # Pre-#18 shape: no identity tuple to fold onto.
    rows = [
        (str(row[0]), str(row[1]), int(row[2]), str(row[3]))
        for row in connection.exec_driver_sql(
            "SELECT id, host, port, proxy_type FROM proxies ORDER BY id",
        ).all()
    ]
    taken = {(host, port, kind) for _id, host, port, kind in rows}
    renamed: list[str] = []
    for proxy_id, host, port, kind in rows:
        try:
            canonical = canonicalize_proxy_host(host)
        except ValueError as exc:
            logger.warning(
                "migration 54: left proxy %s host %r as it is — %s. Re-add the endpoint to "
                "replace it with one the pool can match.",
                proxy_id,
                host,
                exc,
            )
            continue
        if canonical == host:
            continue
        if (canonical, port, kind) in taken:
            # Two spellings of one endpoint. Merging them would silently move or drop
            # account assignments, and ``ix_proxies_identity`` would reject the update
            # anyway — leave both rows and let the operator delete the one they mean.
            logger.warning(
                "migration 54: proxy %s (%r) already exists in the pool as %r on the same "
                "port and type — left unchanged; delete whichever card is stale.",
                proxy_id,
                host,
                canonical,
            )
            continue
        connection.exec_driver_sql(
            "UPDATE proxies SET host = ? WHERE id = ?",
            (canonical, proxy_id),
        )
        taken.discard((host, port, kind))
        taken.add((canonical, port, kind))
        renamed.append(f"{host} -> {canonical}")
    if renamed:
        logger.warning(
            "migration 54: canonicalized %s proxy host(s) so re-adding the same endpoint "
            "refreshes the existing pool card instead of duplicating it: %s",
            len(renamed),
            "; ".join(renamed),
        )
