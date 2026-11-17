"""The session vault by hand: make a key, list an account's copies, restore one.

    uv run python -m tools.session_vault new-key
    uv run python -m tools.session_vault list <session file name without .session>
    uv run python -m tools.session_vault restore <copy.session.enc> <new file.session>

``restore`` never writes over an existing file: restore next to the account's file, stop
Telebuba, then put the restored file in place by hand. Reads ``SESSION_VAULT__KEY`` (and the
folders) from ``.env`` like the app. Prints no key material except the new key it was asked
to make.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.session_vault import SessionVaultError, list_versions, new_vault_key, restore_session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.session_vault")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("new-key", help="print a fresh SESSION_VAULT__KEY")
    listing = commands.add_parser("list", help="the stored copies of one session file")
    listing.add_argument("stem", help="the session file name without .session")
    restore = commands.add_parser("restore", help="decrypt a copy into a NEW file")
    restore.add_argument("version", type=Path)
    restore.add_argument("target", type=Path)
    args = parser.parse_args(argv)

    if args.command == "new-key":
        print(new_vault_key())  # noqa: T201 - the one thing this command is for
        return 0
    if args.command == "list":
        for version in list_versions(args.stem):
            print(version)  # noqa: T201
        return 0
    try:
        restore_session(args.version, args.target)
    except SessionVaultError as exc:
        print(f"refused: {exc.code}", file=sys.stderr)  # noqa: T201
        return 1
    print(f"restored into {args.target}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
