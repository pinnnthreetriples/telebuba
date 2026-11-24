"""The session vault by hand: make a key, list an account's copies, restore one.

    uv run python -m tools.session_vault new-key
    uv run python -m tools.session_vault list <session file name without .session>
    uv run python -m tools.session_vault restore <copy.session.enc> <new file.session>
    uv run python -m tools.session_vault restore-db <copy.db.enc> <new file.db>
    uv run python -m tools.session_vault show-meta <version | .meta | .account file> [--reveal]

``restore`` and ``restore-db`` never write over an existing file: restore next to the
account's file, stop Telebuba, then put the restored file in place by hand. Reads
``SESSION_VAULT__KEY`` (and the folders) from ``.env`` like the app. Prints no key material
except the new key it was asked to make (and, for ``show-meta --reveal``, the passwords).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.db_mirror import restore_db_copy
from core.session_vault import (
    SessionVaultError,
    list_versions,
    meta_path,
    new_vault_key,
    read_bundle,
    restore_session,
)

_SECRET_FIELDS = ("cloud_password", "pending_cloud_password")


def _show_meta(path: Path, *, reveal: bool) -> int:
    """Print a recovery bundle (a version's ``.meta``, or a tombstone); passwords masked."""
    if path.name.endswith(".session.enc"):
        path = meta_path(path)
    try:
        bundle = json.loads(read_bundle(path))
    except (OSError, SessionVaultError, ValueError) as exc:
        print(f"refused: {type(exc).__name__}", file=sys.stderr)  # noqa: T201
        return 1
    if not reveal:
        for field in _SECRET_FIELDS:
            if bundle.get(field):
                bundle[field] = "***"
    print(json.dumps(bundle, indent=2, sort_keys=True))  # noqa: T201
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.session_vault")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("new-key", help="print a fresh SESSION_VAULT__KEY")
    listing = commands.add_parser("list", help="the stored copies of one session file")
    listing.add_argument("stem", help="the session file name without .session")
    restore = commands.add_parser("restore", help="decrypt a copy into a NEW file")
    restore.add_argument("version", type=Path)
    restore.add_argument("target", type=Path)
    restore_db = commands.add_parser("restore-db", help="decrypt a database copy into a NEW file")
    restore_db.add_argument("copy", type=Path)
    restore_db.add_argument("target", type=Path)
    show = commands.add_parser("show-meta", help="print a copy's recovery bundle or a tombstone")
    show.add_argument("path", type=Path)
    show.add_argument("--reveal", action="store_true", help="do not mask the passwords")
    args = parser.parse_args(argv)

    if args.command == "new-key":
        print(new_vault_key())  # noqa: T201 - the one thing this command is for
        return 0
    if args.command == "list":
        for version in list_versions(args.stem):
            print(version)  # noqa: T201
        return 0
    if args.command == "show-meta":
        return _show_meta(args.path, reveal=args.reveal)
    try:
        if args.command == "restore-db":
            restore_db_copy(args.copy, args.target)
        else:
            restore_session(args.version, args.target)
    except SessionVaultError as exc:
        print(f"refused: {exc.code}", file=sys.stderr)  # noqa: T201
        return 1
    print(f"restored into {args.target}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
