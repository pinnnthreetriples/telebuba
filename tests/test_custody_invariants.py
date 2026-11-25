"""Executable custody rules: what may lose an account is a short, pinned list.

Waves 1-2 fixed bugs that cost accounts their key or their 2FA password. These tests keep the
fixes from being undone quietly: each rule is a scan of the real tree plus a self-test that
plants a violation in ``tmp_path`` and proves the scan reports it. Whitelists are exact, and
every whitelisted file must be in ``configs/custody_manifest.toml`` (a change there needs the
owner's ``custody-reviewed`` label, see ``tools/custody_gate.py``).

A failing pin is not a bug in the test: justify the new call in the PR, then update the pin
here and the manifest in the same change.
"""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

import pytest

from tests._custody_scan import (
    MANIFEST,
    ROOT,
    client_constructions,
    column_writers,
    destructive_counts,
    manifest_misses,
    manifest_paths,
    private_imports,
    repo_files,
    symbol_users,
    uncovered_by_manifest,
)

if TYPE_CHECKING:
    from pathlib import Path

# --- a. One place builds a Telethon client ---------------------------------------------------

_CLIENT_SITES = {"core/telegram_client/_client.py"}

# --- b. Calls that end or take over a session, or change who may sign in ---------------------

_TG = "core/telegram_client/"
_DANGEROUS: dict[str, set[str]] = {
    "LogOutRequest": {f"{_TG}_auth.py", f"{_TG}_leftover.py"},
    "ResetAuthorizationRequest": {f"{_TG}_session_close.py"},
    "AcceptLoginTokenRequest": {f"{_TG}_rekey_token.py", f"{_TG}_web_login.py"},
    "ChangeAuthorizationSettingsRequest": {f"{_TG}_rekey_token.py", f"{_TG}_web_login.py"},
    "UpdatePasswordSettingsRequest": {f"{_TG}_twofa.py", f"{_TG}_twofa_email.py"},
    # Reached today only through Telethon's own ``client.qr_login()``; these stay empty so a
    # direct request is a conscious addition.
    "ResetPasswordRequest": set(),
    "DeleteAccountRequest": set(),
    "ExportLoginTokenRequest": set(),
    "ImportLoginTokenRequest": set(),
    # Ends EVERY other session at once, including the one a second device or Telebuba's own
    # web window holds. Never used; never to be.
    "ResetAuthorizationsRequest": set(),
}
# Methods of a client object (matched as attribute access only).
_DANGEROUS_METHODS: dict[str, set[str]] = {
    "log_out": {f"{_TG}_passkey_login.py", f"{_TG}_rekey_undo.py", f"{_TG}_second_device.py"},
    "edit_2fa": set(),
    "qr_login": {f"{_TG}_qr_login.py", f"{_TG}_rekey_token.py"},
}

# --- c. Destructive filesystem calls near session files, per file ----------------------------

_DELETE_SCOPE = (
    "core/telegram_client/",
    "core/session_vault.py",
    "core/web_login/",
    "services/accounts/",
    "services/session_backups.py",
    "services/session_guard/",
    # Pruning of database and key-vault copies is the same loss by another road.
    "core/db_maintenance.py",
    "core/db_mirror.py",
    "core/db_snapshot.py",
    "core/passkey_vault_snapshot.py",
    # A new module named for sessions is counted before anyone lists it in the manifest.
    "core/session_",
    "services/session_",
    "core/session_file.py",
    "core/tdata_import.py",
    "core/passkey_vault.py",
    "core/db.py",
    "core/secure_paths.py",
    "core/_windows_acl.py",
    # Any file the custody manifest matches is in scope too, so a NEW custody module cannot
    # slip past the ratchet (see ``destructive_counts``).
)
# Counted: unlink/rmdir/os.remove/shutil.rmtree/os.removedirs, the overwriting
# os.replace/os.rename/shutil.move, ``.replace(x)``/``.rename(x)`` on a path-like receiver
# (heuristic) and ``client.session.delete()``.
_DELETE_PINS = {
    "core/db_mirror.py": 2,
    "core/db_snapshot.py": 3,
    "core/passkey_vault_snapshot.py": 6,
    "core/session_vault.py": 8,
    "core/tdata_import.py": 1,
    "core/telegram_client/_auth.py": 1,
    "core/telegram_client/_rekey_files.py": 1,
    "core/web_login/_tombstone.py": 1,
    "services/accounts/_key_repair_files.py": 2,
    "services/accounts/_key_repair_rekey.py": 3,
    "services/accounts/_tdata.py": 2,
    "services/accounts/_uploads.py": 3,
    "services/accounts/_web_drive.py": 1,
}

# --- d. The stored 2FA password is written by the repository that owns it --------------------

_TWOFA_COLUMNS = (
    "twofa_password",
    "twofa_password_pending",
    "twofa_pending_at",
    "twofa_pending_refused_at",
)
_TWOFA_WRITERS = {"core/repositories/_accounts_twofa.py"}
# The conservative rule (a function that references the accounts table or a 2FA column AND
# writes through ``.values(**x)``/``.values(name)``/``execute(stmt, rows)``) is satisfied by
# these exact functions; none of them can carry a 2FA column. Each is reviewed by name.
_TWOFA_DYNAMIC_OK = frozenset(
    {
        # Generic writes to other tables that merely mention the accounts table (a join/read).
        ("core/repositories/account_folders/_folders.py", "_add_members"),
        ("core/repositories/device_fingerprint.py", "_record_second_device"),
        ("core/repositories/neurocomment/_account_limits.py", "_save_account_limit_override"),
        ("core/repositories/spam_status.py", "_store_projection"),
        # The accounts table, written with fields the callers name; none is a 2FA column.
        ("core/repositories/_accounts_session_check.py", "_update_account_from_session_check"),
        ("core/repositories/accounts.py", "_create_account"),
        ("core/repositories/accounts.py", "_update_account_profile_snapshot"),
        ("core/repositories/accounts.py", "_update_account_status"),
        # ``execute(account_id, request)`` here is the Telegram call, not a SQL execute.
        ("services/accounts/twofa.py", "set_account_twofa"),
        ("services/accounts/twofa.py", "remove_account_twofa"),
    }
)

# --- e. Private modules are imported through the public facade -------------------------------

_ACCOUNTS_PRIVATE_USERS = {
    "services/device_farm/_phones.py": {"services.accounts._result.AccountActionError"},
    "services/device_farm/_standalone.py": {
        "services.accounts._mobile_start._drop",
        "services.accounts._mobile_start._undo",
        "services.accounts._mobile_start._wait_boot",
        "services.accounts._mobile_start.set_battery",
        "services.accounts._result.AccountActionError",
    },
    "services/inbox_events.py": {"services.accounts._expected_codes.is_login_code_expected"},
    "services/scheduled_posts/_dispatch.py": {
        "services.accounts._result.result_code",
        "services.accounts._story_premium.StoryMedia",
        "services.accounts._story_premium.send_story_for_account",
        "services.accounts._story_premium.story_action",
        "services.accounts._story_premium.with_design",
    },
    "services/scheduled_posts/_policy.py": {
        "services.accounts._result.AccountActionError",
        "services.accounts._result.AccountNotFoundError",
        "services.accounts._story_premium.check_design_limits",
        "services.accounts._story_premium.flat_design",
    },
    "services/session_backups.py": {"services.accounts._recovery_bundle.build_recovery_bundle"},
    "services/session_guard/_loop.py": {"services.accounts._key_repair.repair_key_intents"},
    "services/warming/_mobile_cycle.py": {"services.accounts._result.AccountActionError"},
    "services/warming/_mobile_prep.py": {"services.accounts._result.AccountActionError"},
}
_TELEGRAM_PRIVATE_USERS = {
    "services/accounts/chats.py": {"core.telegram_client._chats"},
    "services/accounts/session_restore.py": {
        "core.telegram_client._session_restore.SessionRestoreRefusedError",
        "core.telegram_client._session_restore.restore_newest_session_copy",
        "core.telegram_client._session_restore.session_restore_state",
    },
}
_FACADE_HINT = "import the public facade instead, or justify the import in the PR and pin it here"


def _unpinned(found: dict[str, set[str]], pinned: dict[str, set[str]]) -> list[str]:
    return [
        f"{rel} imports {symbol}"
        for rel, symbols in sorted(found.items())
        for symbol in sorted(symbols - pinned.get(rel, set()))
    ]


def _plant(root: Path, rel: str, source: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")


# --- tests: a. client construction -----------------------------------------------------------


def test_only_the_client_factory_builds_a_telethon_client() -> None:
    found = set(client_constructions(ROOT))
    assert found == _CLIENT_SITES, (
        f"A Telethon client is built outside the factory: {sorted(found - _CLIENT_SITES)}. "
        "Every client must come from core/telegram_client/_client.py (it opens the session "
        "file safely and refuses calls that end sessions). Justify, pin and add to the manifest."
    )


def test_the_client_guard_catches_a_planted_construction(tmp_path: Path) -> None:
    _plant(
        tmp_path,
        "services/bad.py",
        "from telethon import TelegramClient\n\nclient = TelegramClient('s', 1, 'h')\n",
    )
    assert client_constructions(tmp_path) == {"services/bad.py": 1}


def test_the_client_guard_follows_aliases_and_new_top_level_packages(tmp_path: Path) -> None:
    _plant(tmp_path, "core/ok.py", "x = 1\n")
    _plant(
        tmp_path,
        "jobs/run.py",
        "from telethon import TelegramClient as TC\nfactory = TC\nfactory('s', 1, 'h')\n",
    )
    _plant(tmp_path, "main.py", "import telethon as t\nt.TelegramClient('s', 1, 'h')\n")
    assert client_constructions(tmp_path) == {"jobs/run.py": 1, "main.py": 1}


def test_the_client_guard_follows_a_planted_subclass(tmp_path: Path) -> None:
    _plant(
        tmp_path,
        "core/mine.py",
        "from telethon import TelegramClient\nclass Mine(TelegramClient): ...\n",
    )
    _plant(tmp_path, "api/bad.py", "from core.mine import Mine\nMine('s', 1, 'h')\n")
    assert client_constructions(tmp_path) == {"api/bad.py": 1}


# --- tests: b. dangerous Telegram calls ------------------------------------------------------


def test_dangerous_telegram_calls_live_only_in_pinned_files() -> None:
    users = symbol_users(ROOT, set(_DANGEROUS), set(_DANGEROUS_METHODS))
    expected = {**_DANGEROUS, **_DANGEROUS_METHODS}
    problems = [
        f"{name} used in {sorted(users.get(name, set()) - allowed)}"
        for name, allowed in expected.items()
        if users.get(name, set()) != allowed
    ]
    assert problems == [], (
        "A call that ends a session, takes over a login or changes the 2FA password moved. "
        "Justify it in the PR, then update the pin in tests/test_custody_invariants.py and the "
        f"manifest. ResetAuthorizationsRequest has no allowed file. Details: {problems}"
    )


def test_reset_all_authorizations_is_pinned_to_nowhere() -> None:
    assert _DANGEROUS["ResetAuthorizationsRequest"] == set()


def test_the_dangerous_call_guard_catches_planted_uses(tmp_path: Path) -> None:
    _plant(
        tmp_path,
        "services/bad.py",
        "from telethon.tl.functions.auth import ResetAuthorizationsRequest\n"
        "from telethon.tl import functions\n\n"
        "async def f(client):\n"
        "    await client(functions.auth.LogOutRequest())\n"
        "    await client.log_out()\n",
    )
    found = symbol_users(tmp_path, set(_DANGEROUS), set(_DANGEROUS_METHODS))
    assert found == {
        "ResetAuthorizationsRequest": {"services/bad.py"},
        "LogOutRequest": {"services/bad.py"},
        "log_out": {"services/bad.py"},
    }


def test_a_same_named_function_is_not_a_client_method(tmp_path: Path) -> None:
    _plant(tmp_path, "services/ok.py", "def log_out():\n    return log_out\n")
    assert symbol_users(tmp_path, set(_DANGEROUS), set(_DANGEROUS_METHODS)) == {}


# --- tests: c. session-file deletion ratchet -------------------------------------------------


def test_destructive_filesystem_calls_near_session_files_are_pinned() -> None:
    found = destructive_counts(ROOT, _DELETE_SCOPE)
    drift = {
        rel: (found.get(rel, 0), _DELETE_PINS.get(rel, 0))
        for rel in sorted(found.keys() | _DELETE_PINS.keys())
        if found.get(rel, 0) != _DELETE_PINS.get(rel, 0)
    }
    assert drift == {}, (
        "The number of calls that remove or overwrite a file changed (file: found, pinned). "
        "A deletion or overwrite near session files must be justified in the PR; then re-pin "
        "_DELETE_PINS "
        f"here and the manifest in the same change: {drift}"
    )


def test_the_deletion_ratchet_counts_every_call_form(tmp_path: Path) -> None:
    _plant(
        tmp_path,
        "services/accounts/bad.py",
        "import os\nimport shutil\nfrom os import remove\nfrom pathlib import Path\n\n"
        "def f(p: Path):\n    p.unlink()\n    p.parent.rmdir()\n    os.remove(p)\n"
        "    os.unlink(p)\n    shutil.rmtree(p)\n    remove(p)\n",
    )
    _plant(tmp_path, "services/accounts/fine.py", "def f(items):\n    items.remove(1)\n")
    assert destructive_counts(tmp_path, _DELETE_SCOPE) == {"services/accounts/bad.py": 6}


def test_the_deletion_ratchet_counts_overwrites_and_session_delete(tmp_path: Path) -> None:
    _plant(
        tmp_path,
        "core/session_file.py",
        "import os\nimport shutil as sh\nfrom pathlib import Path\n\n"
        "def f(a: Path, client, text):\n    os.replace(a, a)\n    sh.move(a, a)\n"
        "    Path('x').rename(a)\n    client.session.delete()\n    text.replace('a', 'b')\n",
    )
    assert destructive_counts(tmp_path, ()) == {"core/session_file.py": 4}


# --- tests: d. the 2FA password column -------------------------------------------------------


def test_the_twofa_password_is_written_only_by_its_repository() -> None:
    found = column_writers(ROOT, _TWOFA_COLUMNS, _TWOFA_DYNAMIC_OK)
    assert found == _TWOFA_WRITERS, (
        f"Something else writes a 2FA password column: {sorted(found - _TWOFA_WRITERS)}. "
        "Only core/repositories/_accounts_twofa.py may; route the write through it."
    )


@pytest.mark.parametrize(
    "source",
    [
        "from sqlalchemy import update\nupdate(t).where(t.c.a == 1).values(twofa_password='x')\n",
        "t.insert().values({'twofa_pending_at': 'now'})\n",
        "conn.execute(text('UPDATE accounts SET updated_at = 1, twofa_password = NULL'))\n",
        "conn.exec_driver_sql('INSERT INTO accounts (account_id, twofa_password) VALUES (1, 2)')\n",
    ],
)
def test_the_twofa_guard_catches_planted_writes(tmp_path: Path, source: str) -> None:
    _plant(tmp_path, "services/bad.py", source)
    assert column_writers(tmp_path, _TWOFA_COLUMNS) == {"services/bad.py"}


def test_the_twofa_guard_flags_dynamic_writes_and_honours_the_whitelist(tmp_path: Path) -> None:
    source = "def save(conn, fields):\n    conn.execute(update(_accounts).values(**fields))\n"
    _plant(tmp_path, "services/bad.py", source)
    assert column_writers(tmp_path, _TWOFA_COLUMNS) == {"services/bad.py"}
    allowed = frozenset({("services/bad.py", "save")})
    assert column_writers(tmp_path, _TWOFA_COLUMNS, allowed) == set()


def test_the_twofa_guard_ignores_reads(tmp_path: Path) -> None:
    _plant(
        tmp_path,
        "services/ok.py",
        "select(t.c.twofa_password)\nconn.execute(text('SELECT twofa_password FROM accounts'))\n"
        "t.update().values(updated_at='now')\n",
    )
    assert column_writers(tmp_path, _TWOFA_COLUMNS) == set()


# --- tests: e. facade-only imports -----------------------------------------------------------


def test_private_account_modules_are_imported_only_from_pinned_files() -> None:
    found = private_imports(ROOT, "services.accounts")
    assert _unpinned(found, _ACCOUNTS_PRIVATE_USERS) == [], _FACADE_HINT


def test_private_telegram_client_modules_are_imported_only_from_pinned_files() -> None:
    found = private_imports(ROOT, "core.telegram_client")
    assert _unpinned(found, _TELEGRAM_PRIVATE_USERS) == [], _FACADE_HINT


def test_the_facade_guard_catches_planted_private_imports(tmp_path: Path) -> None:
    _plant(
        tmp_path,
        "api/bad.py",
        "from services.accounts._result import result_code\n"
        "from services.accounts import _uploads\n"
        "from core.telegram_client._client import telegram_client\n"
        "from services.accounts import login\n",
    )
    _plant(tmp_path, "services/accounts/inside.py", "from services.accounts._result import x\n")
    assert private_imports(tmp_path, "services.accounts") == {
        "api/bad.py": {"services.accounts._result.result_code", "services.accounts._uploads"}
    }
    assert private_imports(tmp_path, "core.telegram_client") == {
        "api/bad.py": {"core.telegram_client._client.telegram_client"}
    }
    assert _unpinned(private_imports(tmp_path, "services.accounts"), {}) != []


def test_the_facade_guard_resolves_a_relative_private_import(tmp_path: Path) -> None:
    _plant(tmp_path, "services/other/mod.py", "from ..accounts._result import result_code\n")
    assert private_imports(tmp_path, "services.accounts") == {
        "services/other/mod.py": {"services.accounts._result.result_code"}
    }


# --- tests: f. manifest health and whitelist coverage ----------------------------------------


def test_every_manifest_entry_matches_a_file() -> None:
    assert manifest_misses(manifest_paths(), repo_files(ROOT)) == []


def test_the_manifest_health_check_reports_a_dead_entry() -> None:
    assert manifest_misses(["a/*.py", "gone/**"], {"a/x.py"}) == ["gone/**"]
    assert manifest_misses(["a/*.py"], {"a/b/x.py"}) == ["a/*.py"]


def test_the_manifest_declares_its_label() -> None:
    assert tomllib.loads(MANIFEST.read_text(encoding="utf-8"))["label"].strip() != ""


def test_every_pinned_file_is_covered_by_the_manifest() -> None:
    pinned: set[str] = set(_CLIENT_SITES) | set(_DELETE_PINS)
    pinned |= {rel for files in _DANGEROUS.values() for rel in files}
    pinned |= {rel for files in _DANGEROUS_METHODS.values() for rel in files}
    pinned |= _TWOFA_WRITERS
    assert uncovered_by_manifest(pinned) == []


def test_the_coverage_check_reports_an_uncovered_file() -> None:
    assert uncovered_by_manifest({"services/profile_cards.py"}) == ["services/profile_cards.py"]
