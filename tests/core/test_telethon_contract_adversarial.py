"""Probes of the Telethon contract: planted private use against the tripwire, unpinned facts.

A passing test is a claim that held (or a fact production relies on that the contract did not
pin until now), or a gap in the tripwire that has been closed.
"""

from __future__ import annotations

import ast
import itertools
import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

import pytest
from telethon.sessions import SQLiteSession

from tests.core import test_telethon_contract_tl as tripwire
from tests.core._telethon_contract_support import open_clients, point_settings_at

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    point_settings_at(monkeypatch, tmp_path)


def _run_tripwire_with(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str
) -> AssertionError | None:
    """Run the real tripwire with one extra production file; its failure, if it raises."""
    real = tripwire._production_sources

    def with_planted() -> Iterator[tuple[str, ast.Module]]:
        return itertools.chain(real(), [("services/planted.py", ast.parse(source))])

    monkeypatch.setattr(tripwire, "_production_sources", with_planted)
    clients = open_clients(tmp_path)
    build = next(clients)
    try:
        tripwire.test_no_new_private_telethon_attribute_is_used(build)
    except AssertionError as failure:
        return failure
    finally:
        next(clients, None)  # runs the factory's cleanup
    return None


def test_the_tripwire_is_green_on_the_real_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _run_tripwire_with(monkeypatch, tmp_path, "x = 1\n") is None


@pytest.mark.parametrize(
    "source",
    [
        "def f(c):\n    return c._sender\n",
        "def f(c):\n    return getattr(c, '_updates_handle')\n",
        "from telethon.client import users\n",
        "from telethon.network import mtprotosender\n",
        "from telethon import client\n",
        "from telethon.sessions.sqlite import SQLiteSession\n",
        "def f(c):\n    return c.session._conn\n",
        "async def f(client):\n    sent = await client.send_message(1, 'x')\n    return sent._c\n",
        "def f(listing):\n    for authorization in listing:\n        return authorization._x\n",
    ],
)
def test_new_private_use_and_deep_imports_trip_the_wire(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str
) -> None:
    assert _run_tripwire_with(monkeypatch, tmp_path, source) is not None


@pytest.mark.parametrize(
    "source",
    ["import telethon.network.mtprotosender\n", "import telethon.client.users as u\n"],
)
def test_a_plain_deep_import_trips_the_wire(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str
) -> None:
    assert _run_tripwire_with(monkeypatch, tmp_path, source) is not None


@pytest.mark.parametrize(
    "source",
    ["def f(msg):\n    return msg._client\n", "def f(sender):\n    return sender._state\n"],
)
def test_private_attributes_of_other_telethon_objects_trip_the_wire(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str
) -> None:
    assert _run_tripwire_with(monkeypatch, tmp_path, source) is not None


# ---- facts production relies on that no contract test states ----------------------------


def test_constructing_a_session_creates_the_file(tmp_path: Path) -> None:
    # services/spam_status.py, core/telegram_client/_pool.py and _import_rollback.py guard on
    # this: a probe of a missing account would mint a credential file.
    assert not (tmp_path / "ghost.session").exists()
    session = SQLiteSession(str(tmp_path / "ghost"))
    session.close()
    assert (tmp_path / "ghost.session").exists()


def test_a_closed_session_is_recreated_by_a_new_one_after_unlink(tmp_path: Path) -> None:
    session = SQLiteSession(str(tmp_path / "acc"))
    session.close()
    (tmp_path / "acc.session").unlink()
    again = SQLiteSession(str(tmp_path / "acc"))
    again.close()
    assert (tmp_path / "acc.session").exists()


def test_the_journal_is_a_delete_mode_sidecar_not_wal(tmp_path: Path) -> None:
    # core/telegram_client/_auth.py drops '<path>.session-journal' and nothing else: a WAL
    # default would leave '-wal' / '-shm' holding committed key data beside the quarantine.
    session = SQLiteSession(str(tmp_path / "acc"))
    try:
        with closing(sqlite3.connect(tmp_path / "acc.session")) as db:
            mode = db.execute("pragma journal_mode").fetchone()
    finally:
        session.close()
    assert mode == ("delete",)
    assert not list(tmp_path.glob("acc.session-wal"))
    assert not list(tmp_path.glob("acc.session-shm"))
