"""Who can reach the credential folders, and closing them on command.

    uv run python -m tools.secure_paths check
    uv run python -m tools.secure_paths fix

``check`` lists every configured path (sessions, quarantine, database, backups, browser
profiles, the session vault and its mirror) with the broad groups its access list grants
(everyone, authenticated_users, users, ...); it exits 1 when any path is open. ``fix`` leaves
only the current user, SYSTEM and Administrators on the paths that are open (and only those),
then checks again. The app itself never changes an access list: this command is the only way.
Prints paths and group names, never file contents.
"""

from __future__ import annotations

import argparse
import sys

from core._windows_acl import AclError
from core.private_paths import configured_private_paths
from core.secure_paths import open_private_paths, restrict_private_path

_SUMMARY = "open: {count} of {total} configured paths"


def _report() -> tuple[dict[str, list[str]], int]:
    """Print each path; return the open ones (name to grants) and how many were unreadable."""
    opened: dict[str, list[str]] = {}
    unreadable = 0
    paths = configured_private_paths()
    for name, path in paths.items():
        if not path.exists():
            print(f"{name}: missing ({path})")  # noqa: T201
            continue
        try:
            grants = next((found for _path, found in open_private_paths([path])), [])
        except (AclError, OSError) as exc:
            unreadable += 1
            print(f"{name}: private_path_check_failed ({type(exc).__name__})")  # noqa: T201
            continue
        print(f"{name}: {'open to ' + ', '.join(grants) if grants else 'private'} ({path})")  # noqa: T201
        if grants:
            opened[name] = grants
    print(_SUMMARY.format(count=len(opened), total=len(paths)))  # noqa: T201
    return opened, unreadable


def _fix(opened: dict[str, list[str]]) -> int:
    paths = configured_private_paths()
    failed = 0
    for name in opened:
        try:
            restrict_private_path(paths[name])
        except (AclError, OSError) as exc:
            failed += 1
            print(f"{name}: not changed ({type(exc).__name__}: {exc})", file=sys.stderr)  # noqa: T201
        else:
            print(f"{name}: narrowed to the owner")  # noqa: T201
    return failed


def _run(command: str) -> int:
    opened, unreadable = _report()
    if command == "check" or not opened:
        return 1 if opened or unreadable else 0
    failed = _fix(opened)
    print("checking again:")  # noqa: T201
    again, still_unreadable = _report()
    return 1 if again or still_unreadable or failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.secure_paths")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="list the configured paths other users can reach")
    commands.add_parser("fix", help="narrow only the open paths to the owner, then check again")
    args = parser.parse_args(argv)

    try:
        return _run(args.command)
    except (AclError, OSError) as exc:
        print(f"check failed: {type(exc).__name__}", file=sys.stderr)  # noqa: T201
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
