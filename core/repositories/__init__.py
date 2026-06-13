"""Per-domain data-access repositories (split out of the monolithic core.db).

Each module owns the queries for one domain. Shared plumbing — the SQLAlchemy
metadata, table definitions, engine and small row helpers — stays in
``core.db``; repositories import those internals and ``core.db`` re-exports the
public functions, so existing ``from core.db import ...`` call sites are
unaffected.
"""
