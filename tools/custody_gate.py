"""Custody merge gate: a PR that touches account-custody code needs the owner's label.

``configs/custody_manifest.toml`` lists the modules that can lose an account (its auth key, its
session file, its 2FA password, its authorizations) or guard against that. A pull request
"touches custody" when it changes a path the manifest matches, or when the resolved or required
Telethon version moves (the library under every key operation). Then the label named in the
manifest (``custody-reviewed``) must be on the PR; only the repository owner adds it.

Labels arrive as a JSON array in the ``PR_LABELS`` environment variable, never through the
script text. Anything unparsable counts as "no labels": the gate fails closed.

The PR is judged by ``gate_patterns``: the base manifest joined with the head manifest and the
gate's own files, so a PR cannot exempt itself. Pure functions carry the logic; ``main`` only
reads git (``--base``, ``--head``) and the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib

_MAX_LISTED = 30
_TELETHON = re.compile(r"^\s*telethon(?![\w.-])", re.IGNORECASE)
# The gate's own parts are custody whatever the manifest says: a PR cannot take them off the list.
_ALWAYS = (
    "configs/custody_manifest.toml",
    "tools/custody_gate.py",
    ".github/workflows/custody.yml",
)


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """``**`` crosses directories, ``*`` and ``?`` stay inside one path segment."""
    out: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(out))


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(_glob_regex(pattern).fullmatch(path) for pattern in patterns)


def gate_patterns(base_manifest: str, head_manifest: str) -> tuple[list[str], str]:
    """Patterns and label to judge a PR by: the base branch's list joined with the PR's own.

    A PR that drops an entry from the manifest is still judged by the entry it dropped, and
    the label is the one the base branch names (the PR's only when the base has no manifest).
    """
    patterns: list[str] = list(_ALWAYS)
    label = ""
    for text in (base_manifest, head_manifest):
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            continue
        patterns.extend(str(path) for path in data.get("paths", []))
        label = label or str(data.get("label", ""))
    return list(dict.fromkeys(patterns)), label or "custody-reviewed"


def custody_paths(changed: list[str], patterns: list[str]) -> list[str]:
    """The changed paths that a manifest pattern matches, in input order."""
    return [path for path in changed if matches_any(path, patterns)]


def _telethon_requirements(node: object) -> list[str]:
    if isinstance(node, str):
        return [node.strip()] if _TELETHON.match(node) else []
    if isinstance(node, list):
        return [found for item in node for found in _telethon_requirements(item)]
    if isinstance(node, dict):
        return [found for value in node.values() for found in _telethon_requirements(value)]
    return []


def _required(pyproject_text: str) -> list[str]:
    try:
        data = tomllib.loads(pyproject_text)
    except tomllib.TOMLDecodeError:
        return [pyproject_text]
    project = data.get("project", {})
    found = [
        *_telethon_requirements(project.get("dependencies", [])),
        *_telethon_requirements(project.get("optional-dependencies", {})),
        *_telethon_requirements(data.get("dependency-groups", {})),
    ]
    return sorted(found)


def _locked(lock_text: str) -> list[str]:
    try:
        packages = tomllib.loads(lock_text).get("package", [])
    except tomllib.TOMLDecodeError:
        return [lock_text]
    return sorted(
        json.dumps(
            [str(package.get("version", "")), package.get("source", "")],
            sort_keys=True,
            default=str,
        )
        for package in packages
        if str(package.get("name", "")).lower() == "telethon"
    )


def telethon_changed(
    base_pyproject: str, head_pyproject: str, base_lock: str, head_lock: str
) -> bool:
    """Telethon's requirement line or its locked version or source differs; other bumps do not."""
    return _required(base_pyproject) != _required(head_pyproject) or _locked(base_lock) != _locked(
        head_lock
    )


def parse_labels(raw: str | None) -> list[str]:
    """``PR_LABELS`` as a list of names; empty, missing or malformed means no labels."""
    if not raw or not raw.strip():
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def decide(
    touched: list[str], labels: list[str], label: str, *, telethon_bumped: bool
) -> tuple[int, str]:
    """Exit code and the text to print."""
    if not touched and not telethon_bumped:
        return 0, "custody gate: no custody paths and no Telethon change."
    if label in labels:
        return 0, f"custody gate: custody change, label {label} present."
    lines = [f"custody gate: this PR touches account custody and lacks the label {label}."]
    if telethon_bumped:
        lines.append("  Telethon version changed (pyproject.toml or uv.lock).")
    room = _MAX_LISTED - len(lines) - 2
    lines.extend(f"  {path}" for path in touched[:room])
    if len(touched) > room:
        lines.append(f"  ... and {len(touched) - room} more")
    lines.append(
        f"The repository owner reviews these files and adds the label {label}; "
        "agents must not add it."
    )
    return 1, "\n".join(lines)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
    ).stdout


def _git_show(base: str, name: str) -> str:
    try:
        return _git("show", f"{base}:{name}")
    except subprocess.CalledProcessError:
        return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="git ref the PR merges into")
    parser.add_argument("--head", default="HEAD", help="git ref of the PR head")
    parser.add_argument("--manifest", default="configs/custody_manifest.toml")
    args = parser.parse_args(argv)
    try:
        changed = [
            p
            for p in _git(
                "diff", "--name-only", "--no-renames", "-z", f"{args.base}...{args.head}"
            ).split("\0")
            if p
        ]
    except subprocess.CalledProcessError as error:
        sys.stderr.write(f"custody gate: cannot diff {args.base}...{args.head}: {error.stderr}\n")
        return 2
    patterns, label = gate_patterns(
        _git_show(args.base, args.manifest), _git_show(args.head, args.manifest)
    )
    bumped = telethon_changed(
        _git_show(args.base, "pyproject.toml"),
        _git_show(args.head, "pyproject.toml"),
        _git_show(args.base, "uv.lock"),
        _git_show(args.head, "uv.lock"),
    )
    code, text = decide(
        custody_paths(changed, patterns),
        parse_labels(os.environ.get("PR_LABELS")),
        label,
        telethon_bumped=bumped,
    )
    sys.stdout.write(text + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
