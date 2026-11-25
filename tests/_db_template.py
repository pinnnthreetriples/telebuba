"""One migrated database per test process, copied in where a test would build one.

Building ``telebuba.db`` from nothing (``create_all`` plus every migration) is ~700 SQL
statements, and some 6 000 tests each build one; copying a database that was already
built is ~70. So ``tests/conftest.py`` builds a template once per process through the
production path itself (``configure_database`` → ``_get_engine`` → ``dispose_engine``)
and wraps ``core.db.prepare_schema``: when the production code is about to build the
schema for a file that does not exist yet, the wrapper copies the template there first
and then ALWAYS calls the real ``prepare_schema``, which finds every migration stamped
and creates nothing.

The copy is only taken when nothing a test can change about the build has changed since
the template was made. Any doubt takes the real path:

- the file (or a ``-wal``/``-shm``/``-journal`` beside it) already exists — a test that
  planted an old database there must migrate THAT one;
- ``MIGRATIONS`` in ``core.migrations`` or ``core.db_snapshot`` is not the tuple the
  template was built with;
- the tables, columns or indexes registered on ``_metadata`` differ, or ``create_tables``
  is not the ``_metadata.create_all`` the template used;
- any global of ``core.db_snapshot`` or a ``core.migration*`` module was replaced (a
  spied migration step, a frozen clock, a failing ``apply_migrations``);
- the test file is about building the database (``FRESH_DB_PATHS``), the test carries
  ``@pytest.mark.fresh_database``, or ``TELEBUBA_TEST_FRESH_DB=1`` is set.

What a copy cannot reproduce, and why each is safe here: ``schema_version.applied_at`` is
the template's build time; ``core.db_snapshot._needs_snapshot`` answers "all stamped"
instead of "no tables" (both mean no snapshot); a migration's side effects (its logs, a
call to a step) happen once at template time, not in the test; the window in which two
threads race the first build under ``_ENGINE_LOCK`` is shorter. Tests that look at any of
these run the real path through the rules above.
``tests/test_db_template.py`` pins the mechanism and that a copy equals a fresh build.
"""

from __future__ import annotations

import importlib
import os
import shutil
import sys
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING

import core.db as core_db
import core.db_snapshot as core_db_snapshot
import core.migrations as core_migrations

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

FRESH_DB_ENV = "TELEBUBA_TEST_FRESH_DB"
FRESH_DB_MARKER = "fresh_database"
# Test files about building the database itself. Matched against the path relative to the
# repository root, so a new ``test_migrations_*.py`` takes the real path without a marker.
FRESH_DB_PATHS = (
    "tests/core/test_db*.py",
    "tests/core/test_migrations*.py",
    "tests/core/repositories/neurocomment/test_schema_migrations.py",
    "tests/core/repositories/neurocomment/test_settings_comment_mode_migration.py",
)
_SIDECARS = ("-wal", "-shm", "-journal")
_PIPELINE_MODULES = ("core.db_snapshot", "core.migration")

type _CreateTables = Callable[[Engine], None]
type _PrepareSchema = Callable[[Engine, _CreateTables], None]


def fresh_database_forced() -> bool:
    """The suite-wide switch: every database is built the real way."""
    return os.environ.get(FRESH_DB_ENV, "") == "1"


def wants_fresh_database(relative_path: str) -> bool:
    """Whether a test file is one of ``FRESH_DB_PATHS`` (a repo-relative POSIX path)."""
    return any(fnmatch(relative_path, pattern) for pattern in FRESH_DB_PATHS)


def _schema_fingerprint() -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]:
    """Each table on ``_metadata`` with its columns and indexes: the table set and more."""
    return tuple(
        (name, tuple(table.columns.keys()), tuple(sorted(str(i.name) for i in table.indexes)))
        for name, table in sorted(core_db._metadata.tables.items())
    )


def _pipeline_globals() -> dict[tuple[str, str], object]:
    """Every global of the modules that build a schema, by identity."""
    # Copies, not views: another thread may import (and so add globals) while this runs.
    return {
        (module_name, attribute): value
        for module_name, module in sys.modules.copy().items()
        if module_name.startswith(_PIPELINE_MODULES)
        for attribute, value in vars(module).copy().items()
    }


@dataclass(frozen=True)
class DatabaseTemplate:
    path: Path
    migrations: tuple[object, ...]
    schema: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]
    create_tables: _CreateTables
    pipeline: dict[tuple[str, str], object]

    def matches(self, engine: Engine, create_tables: _CreateTables) -> bool:
        """Whether a copy of the template is exactly what this build would produce."""
        database = engine.url.database
        if engine.url.get_backend_name() != "sqlite" or database in {None, "", ":memory:"}:
            return False
        if any(Path(f"{database}{suffix}").exists() for suffix in ("", *_SIDECARS)):
            return False
        return (
            not fresh_database_forced()
            and create_tables == self.create_tables
            and tuple(core_migrations.MIGRATIONS) == self.migrations
            and tuple(core_db_snapshot.MIGRATIONS) == self.migrations
            and _schema_fingerprint() == self.schema
            and self._pipeline_untouched()
        )

    def _pipeline_untouched(self) -> bool:
        current = _pipeline_globals()
        return current.keys() == self.pipeline.keys() and all(
            current[key] is value for key, value in self.pipeline.items()
        )


def build_template(directory: Path) -> DatabaseTemplate:
    """Build the template the production way; the caller re-points the engine afterwards.

    Tables register on ``_metadata`` as their repository modules are imported, and many are
    imported lazily, so a bare run of one test file holds fewer of them than the app does.
    The app imports ``api`` before its first build; so does the template, or a later lazy
    import would change the table set and every copy would be declined.
    """
    importlib.import_module("api")
    path = directory / "telebuba.db"
    core_db.configure_database(path)
    core_db._get_engine()
    core_db.dispose_engine()
    # dispose closes the last connection, which checkpoints the WAL into the main file and
    # removes it. A sidecar left behind would hold rows a bare file copy loses.
    leftovers = [f"{path}{suffix}" for suffix in _SIDECARS if Path(f"{path}{suffix}").exists()]
    if leftovers:
        msg = f"database template not self-contained after dispose: {leftovers}"
        raise RuntimeError(msg)
    return DatabaseTemplate(
        path=path,
        migrations=tuple(core_migrations.MIGRATIONS),
        schema=_schema_fingerprint(),
        create_tables=core_db._metadata.create_all,
        pipeline=_pipeline_globals(),
    )


def copy_template(template: DatabaseTemplate, target: Path) -> None:
    shutil.copyfile(template.path, target)


def prepare_schema_from(template: DatabaseTemplate, real: _PrepareSchema) -> _PrepareSchema:
    """``real`` with the template copied in first, when the template matches the build."""

    def prepare_schema(engine: Engine, create_tables: _CreateTables) -> None:
        if template.matches(engine, create_tables):
            copy_template(template, Path(str(engine.url.database)))
        real(engine, create_tables)

    return prepare_schema
