"""Adversarial probes of the custody scans: plant a violation, expect the scan to see it.

A passing test is a claim that held, or a bypass that has been closed. What remains is a
documented limitation: ``xfail(strict=False, reason="documented limitation: ...")``, listed under
"What the scans do not see" in ``tests/_custody_scan.py``. Those are deliberate evasions
(``getattr`` with built strings, ``importlib``, ``partial``, ``subprocess``) that an honest
change does not write; if one starts to pass nothing breaks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests._custody_scan import (
    client_constructions,
    column_writers,
    destructive_counts,
    private_imports,
    symbol_users,
)
from tests.test_custody_invariants import (
    _DANGEROUS,
    _DANGEROUS_METHODS,
    _DELETE_SCOPE,
    _TWOFA_COLUMNS,
)

if TYPE_CHECKING:
    from pathlib import Path

_TG = "from telethon import TelegramClient\n"


def _plant(root: Path, rel: str, source: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")


def _danger(root: Path) -> dict[str, set[str]]:
    return symbol_users(root, set(_DANGEROUS), set(_DANGEROUS_METHODS))


# ---- a. client construction -------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        _TG + "c = TelegramClient('s', 1, 'h')\n",
        "import telethon\nc = telethon.TelegramClient('s', 1, 'h')\n",
        "from telethon.sync import TelegramClient\nc = TelegramClient('s', 1, 'h')\n",
        "from telethon.client.telegramclient import TelegramClient\nTelegramClient('s',1,'h')\n",
    ],
)
def test_direct_constructions_are_seen(tmp_path: Path, source: str) -> None:
    _plant(tmp_path, "services/x.py", source)
    assert client_constructions(tmp_path) == {"services/x.py": 1}


def test_an_import_alias_of_the_client_class_is_seen(tmp_path: Path) -> None:
    _plant(
        tmp_path, "services/x.py", "from telethon import TelegramClient as TC\nTC('s', 1, 'h')\n"
    )
    assert client_constructions(tmp_path) == {"services/x.py": 1}


def test_a_client_class_assigned_to_a_name_is_seen(tmp_path: Path) -> None:
    _plant(tmp_path, "services/x.py", _TG + "factory = TelegramClient\nc = factory('s', 1, 'h')\n")
    assert client_constructions(tmp_path) != {}


@pytest.mark.xfail(
    strict=False,
    reason="documented limitation: functools.partial(TelegramClient, ...) calls no known name",
)
def test_a_client_class_wrapped_in_partial_is_seen(tmp_path: Path) -> None:
    source = (
        _TG + "import functools\nmake = functools.partial(TelegramClient, 's', 1, 'h')\nmake()\n"
    )
    _plant(tmp_path, "services/x.py", source)
    assert client_constructions(tmp_path) != {}


@pytest.mark.parametrize("rel", ["jobs/run.py", "main.py", "scripts/fix.py"])
def test_a_construction_outside_the_scanned_layers_is_seen(tmp_path: Path, rel: str) -> None:
    (tmp_path / "core").mkdir()
    _plant(tmp_path, rel, _TG + "c = TelegramClient('s', 1, 'h')\n")
    assert client_constructions(tmp_path) != {}


# ---- b. dangerous requests --------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "from telethon.tl.functions.auth import ResetAuthorizationsRequest as R\nR()\n",
        "from telethon.tl import functions as f\nf.auth.ResetAuthorizationsRequest()\n",
        "import telethon.tl.functions.auth as a\na.ResetAuthorizationsRequest()\n",
        "from telethon.tl.functions.auth import *\nResetAuthorizationsRequest()\n",
        "from telethon.tl.functions import auth\nauth.ResetAuthorizationsRequest()\n",
        "import telethon\ntelethon.tl.functions.auth.ResetAuthorizationsRequest()\n",
    ],
)
def test_reset_all_authorizations_is_seen_through_aliases(tmp_path: Path, source: str) -> None:
    _plant(tmp_path, "services/x.py", source)
    assert _danger(tmp_path).get("ResetAuthorizationsRequest") == {"services/x.py"}


@pytest.mark.parametrize(
    "source",
    [
        "async def f(c):\n    await c.log_out()\n",
        "async def f(c):\n    m = c.log_out\n    await m()\n",
        "from telethon.tl.functions.account import DeleteAccountRequest as D\nD('x')\n",
    ],
)
def test_other_dangerous_calls_are_seen(tmp_path: Path, source: str) -> None:
    _plant(tmp_path, "services/x.py", source)
    assert _danger(tmp_path) != {}


def test_session_delete_is_seen(tmp_path: Path) -> None:
    _plant(tmp_path, "services/accounts/x.py", "def f(client):\n    client.session.delete()\n")
    found = _danger(tmp_path)
    found.update({f"unlink@{rel}": {rel} for rel in destructive_counts(tmp_path, _DELETE_SCOPE)})
    assert found != {}


@pytest.mark.xfail(
    strict=False,
    reason="documented limitation: a request named by string (getattr(functions.auth, "
    "'Reset' + ...), __dict__[...], importlib) is not an Attribute/Name/alias node",
)
@pytest.mark.parametrize(
    "source",
    [
        "from telethon.tl import functions\n"
        "cls = getattr(functions.auth, 'ResetAuthorizations' + 'Request')\n",
        "from telethon.tl import functions\n"
        "cls = functions.auth.__dict__['ResetAuthorizationsRequest']\n",
        "import importlib\nm = importlib.import_module('telethon.tl.functions.auth')\n"
        "cls = getattr(m, 'ResetAuthorizationsRequest')\n",
    ],
    ids=["getattr", "dict", "importlib"],
)
def test_a_request_named_by_string_is_seen(tmp_path: Path, source: str) -> None:
    _plant(tmp_path, "services/x.py", source)
    assert _danger(tmp_path) != {}


# ---- c. destructive filesystem calls ----------------------------------------------------

_FORMS_IN_SCOPE = {
    "unlink": "from pathlib import Path\ndef f(p: Path):\n    p.unlink()\n",
    "os.remove": "import os\ndef f(p):\n    os.remove(p)\n",
    "rmtree_from": "from shutil import rmtree as rt\ndef f(p):\n    rt(p)\n",
}


@pytest.mark.parametrize("form", sorted(_FORMS_IN_SCOPE))
def test_the_common_delete_forms_are_counted(tmp_path: Path, form: str) -> None:
    _plant(tmp_path, "services/accounts/x.py", _FORMS_IN_SCOPE[form])
    assert destructive_counts(tmp_path, _DELETE_SCOPE) == {"services/accounts/x.py": 1}


_OVERWRITE_FORMS = {
    "module_alias_rmtree": "import shutil as sh\ndef f(p):\n    sh.rmtree(p)\n",
    "os_replace": "import os\ndef f(a, b):\n    os.replace(a, b)\n",
    "os_rename": "import os\ndef f(a, b):\n    os.rename(a, b)\n",
    "shutil_move": "import shutil\ndef f(a, b):\n    shutil.move(a, b)\n",
    "path_replace": "from pathlib import Path\ndef f(a: Path, b):\n    a.replace(b)\n",
    "path_rename": "from pathlib import Path\ndef f(a: Path, b):\n    a.rename(b)\n",
    "os_removedirs": "import os\ndef f(p):\n    os.removedirs(p)\n",
    "send2trash": "from send2trash import send2trash\ndef f(p):\n    send2trash(p)\n",
}


@pytest.mark.parametrize("form", sorted(_OVERWRITE_FORMS))
def test_overwriting_and_aliased_removals_are_counted(tmp_path: Path, form: str) -> None:
    _plant(tmp_path, "services/accounts/x.py", _OVERWRITE_FORMS[form])
    assert destructive_counts(tmp_path, _DELETE_SCOPE) == {"services/accounts/x.py": 1}


_UNSEEN_FORMS = {
    "subprocess_rm": "import subprocess\ndef f(p):\n    subprocess.run(['rm', '-rf', p])\n",
    "subprocess_del": "import subprocess\ndef f(p):\n    subprocess.run(['cmd', '/c', 'del', p])\n",
    "truncate": "from pathlib import Path\ndef f(p: Path):\n    p.write_bytes(b'')\n",
    "unlink_partial": "import functools\nfrom pathlib import Path\n"
    "def f(p: Path):\n    functools.partial(Path.unlink, p)()\n",
    "methodcaller": "import operator\ndef f(p):\n    operator.methodcaller('unlink')(p)\n",
}


@pytest.mark.xfail(
    strict=False,
    reason="documented limitation: a file removed through subprocess, emptied with "
    "write_bytes(b''), or reached through partial/methodcaller indirection",
)
@pytest.mark.parametrize("form", sorted(_UNSEEN_FORMS))
def test_other_ways_to_destroy_a_file_are_counted(tmp_path: Path, form: str) -> None:
    _plant(tmp_path, "services/accounts/x.py", _UNSEEN_FORMS[form])
    assert destructive_counts(tmp_path, _DELETE_SCOPE) != {}


@pytest.mark.parametrize(
    "rel",
    [
        "core/session_file.py",
        "core/tdata_import.py",
        "core/passkey_vault.py",
        "core/db.py",
        "services/session_tools.py",
    ],
)
def test_a_delete_in_an_unlisted_module_is_counted(tmp_path: Path, rel: str) -> None:
    _plant(tmp_path, rel, "from pathlib import Path\ndef f(p: Path):\n    p.unlink()\n")
    assert destructive_counts(tmp_path, _DELETE_SCOPE) != {}


# ---- d. the 2FA password column ---------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "stmt = t.update().values(twofa_password='x')\n",
        "stmt = t.insert().values({'twofa_password': 'x'})\n",
        'conn.execute(text("UPDATE accounts SET twofa_password = :p"))\n',
        "conn.execute(text(f'UPDATE accounts SET twofa_password = {x}'))\n",
        "sql = ('UPDATE accounts '\n       'SET twofa_password_pending = 1')\n",
    ],
)
def test_the_usual_writes_are_seen(tmp_path: Path, source: str) -> None:
    _plant(tmp_path, "services/x.py", source)
    assert column_writers(tmp_path, _TWOFA_COLUMNS) == {"services/x.py"}


@pytest.mark.parametrize(
    "source",
    [
        "row = {'twofa_password': 'x'}\nstmt = t.update().values(**row)\n",
        "row = {'twofa_password': 'x'}\nstmt = t.update().values(row)\n",
        "rows = [{'twofa_password': 'x'}]\nconn.execute(t.insert(), rows)\n",
        "fields = dict(twofa_password='x')\nconn.execute(t.update().values(fields))\n",
    ],
    ids=["kwargs", "named_dict", "executemany", "dict_call"],
)
def test_a_write_through_a_prebuilt_dict_is_seen(tmp_path: Path, source: str) -> None:
    _plant(tmp_path, "services/x.py", source)
    assert column_writers(tmp_path, _TWOFA_COLUMNS) == {"services/x.py"}


@pytest.mark.parametrize(
    "source",
    [
        "stmt = ins.on_conflict_do_update(index_elements=['id'], set_={'twofa_password': 'x'})\n",
        "def f(row):\n    row.twofa_password = 'x'\n",
        "def f(row):\n    setattr(row, 'twofa_password', 'x')\n",
    ],
    ids=["upsert", "attr_assign", "setattr"],
)
def test_other_write_shapes_are_seen(tmp_path: Path, source: str) -> None:
    _plant(tmp_path, "services/x.py", source)
    assert column_writers(tmp_path, _TWOFA_COLUMNS) == {"services/x.py"}


@pytest.mark.xfail(
    strict=False,
    reason="documented limitation: string-built SQL ('UPDATE accounts SET ' + col + ' = ?') "
    "never holds the column name in one constant",
)
def test_string_built_sql_is_seen(tmp_path: Path) -> None:
    _plant(
        tmp_path,
        "services/x.py",
        "col = 'twofa_' + 'password'\nsql = 'UPDATE accounts SET ' + col + ' = ?'\n",
    )
    assert column_writers(tmp_path, _TWOFA_COLUMNS) == {"services/x.py"}


# ---- e. private imports -----------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "from services.accounts import _result\n",
        "from services.accounts import _result as r\n",
        "import services.accounts._result as r\n",
        "import services.accounts._result\n",
        "from services.accounts._result import *\n",
        "from ..accounts._result import x\n",
        "from ..accounts import _result\n",
    ],
)
def test_private_import_spellings_are_seen(tmp_path: Path, source: str) -> None:
    _plant(tmp_path, "services/other/m.py", source)
    assert private_imports(tmp_path, "services.accounts") != {}


@pytest.mark.xfail(
    strict=False,
    reason="documented limitation: importlib.import_module('services.accounts._x') and "
    "attribute reach-through ('import services.accounts as a; a._result') are not imports "
    "of a private name",
)
@pytest.mark.parametrize(
    "source",
    [
        "import importlib\nm = importlib.import_module('services.accounts._result')\n",
        "import services.accounts as a\nx = a._result\n",
    ],
)
def test_private_reach_through_without_an_import_statement_is_seen(
    tmp_path: Path, source: str
) -> None:
    _plant(tmp_path, "services/other/m.py", source)
    assert private_imports(tmp_path, "services.accounts") != {}
