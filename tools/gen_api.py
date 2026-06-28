"""Regenerate the typed frontend API client from the live backend OpenAPI schema.

Pipeline (the split ADR's contract: pydantic -> FastAPI OpenAPI -> typed client):

1. Dump the OpenAPI document from the pure ``api`` app (``create_app()``), so the
   SPA catch-all in ``main.py`` is excluded and only ``/api/v1`` routes appear.
2. Run the frontend ``gen:api`` script (``@hey-api/openapi-ts`` + prettier), which
   writes the typed client + TanStack Query options into ``frontend/src/shared/api``.

CI runs this and then ``git diff --exit-code``: a stale committed client fails the
build. Output is written with LF newlines so a Windows run matches CI's Linux run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from api import create_app

_ROOT = Path(__file__).resolve().parent.parent
_FRONTEND = _ROOT / "frontend"
_SCHEMA = _FRONTEND / "openapi.json"


def main() -> int:
    schema = create_app().openapi()
    _SCHEMA.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8", newline="\n")

    npm = shutil.which("npm")
    if npm is None:
        sys.stderr.write("npm not found on PATH; install Node to run gen:api\n")
        return 1
    subprocess.run([npm, "run", "gen:api"], cwd=_FRONTEND, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
