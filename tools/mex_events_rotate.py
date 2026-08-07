"""Archive old `mex log` events out of the live timeline.

``.mex/events/decisions.jsonl`` is append-only and mex ships no rotation: every
reader (``mex timeline``, the MCP timeline tool) calls ``readEvents``, which parses
the WHOLE file and only then applies ``--since`` / ``--limit`` / ``--kind``. The file
is small today, so this exists to keep it that way rather than to fix a problem:
one entry is ~880 bytes and the log grows with every decision worth recording.

Rotation is by calendar year into a sibling ``decisions-<year>.jsonl``. Years, not a
row count, because the thing an archive has to answer is "what did we decide back
then" — a "keep the last N" rule silently drops the oldest rationale, which is
exactly the rationale nobody remembers and everybody re-litigates. Nothing is
deleted; the archive stays in git next to the live file.

The current year always stays live, so a rotation never hides a decision from the
timeline an agent is reading this session.

    python3 tools/mex_events_rotate.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

_EVENTS = Path(".mex/events")
_LIVE = _EVENTS / "decisions.jsonl"


def _say(message: str) -> None:
    sys.stdout.write(f"{message}\n")


def _year(line: str) -> str | None:
    """The event's calendar year, or None for a row we must not move."""
    try:
        stamp = json.loads(line)["timestamp"]
    except (json.JSONDecodeError, KeyError, TypeError):
        # A row we cannot read is a row we cannot file. Leaving it live keeps it
        # visible and keeps this script from being a quiet way to lose data.
        return None
    return str(stamp)[:4] or None


def main() -> int:
    if not _LIVE.exists():
        _say(f"{_LIVE}: nothing to rotate")
        return 0

    lines = [line for line in _LIVE.read_text(encoding="utf-8").splitlines() if line.strip()]
    current = str(datetime.now(UTC).year)

    keep: list[str] = []
    archive: dict[str, list[str]] = defaultdict(list)
    for line in lines:
        year = _year(line)
        if year is None or year >= current:
            keep.append(line)
        else:
            archive[year].append(line)

    if not archive:
        _say(f"{_LIVE}: {len(keep)} event(s), nothing older than {current}")
        return 0

    for year, rows in sorted(archive.items()):
        target = _EVENTS / f"decisions-{year}.jsonl"
        existing = (
            [ln for ln in target.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if target.exists()
            else []
        )
        # Append, never replace: a second run in the same year must not drop what a
        # first run already filed.
        target.write_text("\n".join([*existing, *rows]) + "\n", encoding="utf-8")
        _say(f"{target}: +{len(rows)} event(s)")

    _LIVE.write_text(("\n".join(keep) + "\n") if keep else "", encoding="utf-8")
    _say(f"{_LIVE}: {len(keep)} event(s) kept live")
    return 0


if __name__ == "__main__":
    sys.exit(main())
