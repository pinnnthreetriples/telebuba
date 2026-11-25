"""The per-process database template (``tests/_db_template.py``) and its fallbacks.

Lives at ``tests/`` on purpose: ``tests/core/test_db*.py`` always builds for real, and
the first test here must see the copy taken.

A copy is recognised by what it skips: a database built from nothing stamps every
migration (one ``INSERT INTO schema_version`` each), a copy of the template stamps none.
That is a count of statements, not a timing, so the speed-up cannot switch off silently.
"""

from __future__ import annotations

import ast
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import Column, Index, Integer, Table, create_engine, event
from sqlalchemy.engine import Engine

import core.db as core_db
import core.db_snapshot as core_db_snapshot
import core.migrations as core_migrations
from tests import _db_template

if TYPE_CHECKING:
    from collections.abc import Iterator

_ROOT = Path(__file__).resolve().parent.parent
_STAMP = "INSERT INTO schema_version"
# Under the switch there is no template to copy, which is the point of the switch.
copies = pytest.mark.skipif(
    _db_template.fresh_database_forced(),
    reason=f"{_db_template.FRESH_DB_ENV}=1 builds every database",
)


@pytest.fixture
def stamps() -> Iterator[list[str]]:
    """Every ``schema_version`` stamp any engine executes while the test runs."""
    seen: list[str] = []

    def _record(*args: object) -> None:
        statement = str(args[2])
        if statement.startswith(_STAMP):
            seen.append(statement)

    event.listen(Engine, "before_cursor_execute", _record)
    yield seen
    event.remove(Engine, "before_cursor_execute", _record)


def _build(path: Path) -> None:
    core_db.configure_database(path)
    core_db._get_engine()
    core_db.dispose_engine()


def _stamped_versions(path: Path) -> list[int]:
    with closing(sqlite3.connect(path)) as connection:
        return [row[0] for row in connection.execute("SELECT version FROM schema_version")]


@copies
def test_a_new_database_is_a_copy_of_the_template(tmp_path: Path, stamps: list[str]) -> None:
    _build(tmp_path / "new.db")

    assert stamps == []
    assert len(_stamped_versions(tmp_path / "new.db")) == len(core_migrations.MIGRATIONS)


@pytest.mark.fresh_database
def test_the_marker_builds_from_nothing(tmp_path: Path, stamps: list[str]) -> None:
    _build(tmp_path / "new.db")

    assert len(stamps) == len(core_migrations.MIGRATIONS)


def test_the_switch_builds_from_nothing(
    tmp_path: Path, stamps: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_db_template.FRESH_DB_ENV, "1")

    _build(tmp_path / "new.db")

    assert len(stamps) == len(core_migrations.MIGRATIONS)


def test_an_existing_database_is_migrated_not_replaced(tmp_path: Path, stamps: list[str]) -> None:
    path = tmp_path / "old.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE legacy (kept INTEGER)")
        connection.execute("INSERT INTO legacy VALUES (7)")
        connection.commit()

    _build(path)

    assert len(stamps) == len(core_migrations.MIGRATIONS)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT kept FROM legacy").fetchall() == [(7,)]


@pytest.mark.parametrize(
    "modules",
    [(core_migrations,), (core_db_snapshot,), (core_migrations, core_db_snapshot)],
    ids=["migrations", "db_snapshot", "both"],
)
def test_a_patched_registry_builds_from_nothing(
    tmp_path: Path,
    stamps: list[str],
    monkeypatch: pytest.MonkeyPatch,
    modules: tuple[object, ...],
) -> None:
    shorter = core_migrations.MIGRATIONS[:-1]
    for module in modules:
        monkeypatch.setattr(module, "MIGRATIONS", shorter)

    _build(tmp_path / "new.db")

    expected = len(shorter) if core_migrations in modules else len(core_migrations.MIGRATIONS)
    assert len(stamps) == expected
    assert len(_stamped_versions(tmp_path / "new.db")) == expected


def test_a_patched_build_step_builds_from_nothing(
    tmp_path: Path, stamps: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []
    real = core_db_snapshot.snapshot_before_migrations

    def _spy(engine: Engine) -> object:
        calls.append(engine)
        return real(engine)

    monkeypatch.setattr(core_db_snapshot, "snapshot_before_migrations", _spy)

    _build(tmp_path / "new.db")

    assert len(calls) == 1
    assert len(stamps) == len(core_migrations.MIGRATIONS)


def test_a_new_table_builds_from_nothing(tmp_path: Path, stamps: list[str]) -> None:
    table = Table("template_probe", core_db._metadata, Column("id", Integer, primary_key=True))
    try:
        _build(tmp_path / "new.db")
    finally:
        core_db._metadata.remove(table)

    assert len(stamps) == len(core_migrations.MIGRATIONS)


def test_a_new_index_on_an_existing_table_builds_from_nothing(
    tmp_path: Path, stamps: list[str]
) -> None:
    """``create_all`` skips an existing table, index included: a copy would lack it."""
    accounts = core_db._metadata.tables["accounts"]
    index = Index("ix_template_probe", accounts.c.account_id)
    try:
        _build(tmp_path / "new.db")
    finally:
        accounts.indexes.discard(index)

    assert len(stamps) == len(core_migrations.MIGRATIONS)
    with closing(sqlite3.connect(tmp_path / "new.db")) as connection:
        names = {row[1] for row in connection.execute("PRAGMA index_list(accounts)")}
    assert "ix_template_probe" in names


@copies
@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_any_file_at_the_path_declines_the_copy(
    tmp_path: Path, database_template: _db_template.DatabaseTemplate, suffix: str
) -> None:
    target = tmp_path / "new.db"
    engine = create_engine(f"sqlite:///{target}")
    create_tables = core_db._metadata.create_all
    assert database_template.matches(engine, create_tables)

    Path(f"{target}{suffix}").write_bytes(b"")

    assert not database_template.matches(engine, create_tables)
    engine.dispose()


@copies
def test_other_builds_decline_the_copy(
    tmp_path: Path, database_template: _db_template.DatabaseTemplate
) -> None:
    memory = create_engine("sqlite://")
    on_disk = create_engine(f"sqlite:///{tmp_path / 'new.db'}")

    assert not database_template.matches(memory, core_db._metadata.create_all)
    assert not database_template.matches(on_disk, lambda _engine: None)
    memory.dispose()
    on_disk.dispose()


@pytest.mark.parametrize(
    ("path", "fresh"),
    [
        ("tests/core/test_db_snapshot_migration.py", True),
        ("tests/core/test_migrations_some_future_step.py", True),
        ("tests/core/repositories/neurocomment/test_schema_migrations.py", True),
        ("tests/core/repositories/neurocomment/test_inbox.py", False),
        ("tests/test_db_template.py", False),
    ],
)
def test_files_about_the_build_always_build_from_nothing(path: str, *, fresh: bool) -> None:
    assert _db_template.wants_fresh_database(path) is fresh


def _dump(path: Path) -> dict[str, object]:
    """Everything a test could read from the file, minus the stamps' wall-clock time."""
    with closing(sqlite3.connect(path)) as connection:
        dump: dict[str, object] = {
            pragma: connection.execute(f"PRAGMA {pragma}").fetchone()[0]
            for pragma in ("journal_mode", "user_version", "page_size", "application_id")
        }
        dump["sqlite_master"] = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        tables = [
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        for table in tables:
            columns = [
                f'"{row[1]}"'
                for row in connection.execute(f'PRAGMA table_info("{table}")')
                if (table, row[1]) != ("schema_version", "applied_at")
            ]
            query = f'SELECT {", ".join(columns)} FROM "{table}"'  # noqa: S608 - names from sqlite_master
            dump[table] = sorted(connection.execute(query).fetchall(), key=repr)
    return dump


def test_a_copy_equals_a_database_built_from_nothing(
    tmp_path: Path, stamps: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sentinel: a migration whose result depends on when or where it runs turns this red.

    Builds its own template the way the session fixture does, so it also runs under the switch.
    """
    monkeypatch.delenv(_db_template.FRESH_DB_ENV, raising=False)
    (tmp_path / "template").mkdir()
    template = _db_template.build_template(tmp_path / "template")
    stamps.clear()
    real = core_db_snapshot.prepare_schema
    monkeypatch.setattr(core_db, "prepare_schema", _db_template.prepare_schema_from(template, real))
    _build(tmp_path / "copied.db")
    assert stamps == []

    monkeypatch.setattr(core_db, "prepare_schema", real)
    _build(tmp_path / "built.db")
    assert len(stamps) == len(core_migrations.MIGRATIONS)

    assert _dump(tmp_path / "copied.db") == _dump(tmp_path / "built.db")


@copies
def test_the_template_saw_the_registry_as_imported(
    database_template: _db_template.DatabaseTemplate,
) -> None:
    assert database_template.migrations == core_migrations.MIGRATIONS
    assert database_template.schema == _db_template._schema_fingerprint()


def test_migrations_read_no_configuration_or_randomness_beyond_their_backfills() -> None:
    """Migrations read no settings, environment or randomness, so a template is not stale.

    One that did could build one schema in the template and another under a test's
    monkeypatch, and the copy would hide the difference.
    """
    allowed = {"core/migration_steps_pool.py": {"uuid"}}
    forbidden = {"core.config", "os", "random", "secrets", "uuid"}
    offenders: dict[str, set[str]] = {}
    for path in sorted((_ROOT / "core").glob("migration*.py")):
        imported: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)  # ``from core import config`` counts as ``core.config``
                imported.update(f"{node.module}.{alias.name}" for alias in node.names)
        relative = path.relative_to(_ROOT).as_posix()
        hits = (imported & forbidden) - allowed.get(relative, set())
        if hits:
            offenders[relative] = hits
    assert offenders == {}
