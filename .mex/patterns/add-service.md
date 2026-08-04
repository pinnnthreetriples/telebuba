---
last_updated: 2026-08-04
---

# Add Service
1. Define cross-layer Pydantic contracts.
2. Add logic to `services/<domain>.py` or a focused package submodule; keep `__init__.py` re-export-only (the package facades under `accounts/`, `warming/`, `neurocomment/` carry a layout docstring plus imports and `__all__`, nothing else).
3. Delegate DB, Telegram, providers, config and logging to `core/` gateways. In a package, route the injectable collaborators (`execute`, `generate_text`, `rng`, …) through the domain's `_seams.py` so tests patch one place instead of every submodule.
4. Add tests mocking gateways and covering success/failure branches. Patch a collaborator on the submodule that owns the name (`services.accounts.sessions.convert_tdata_zip`), never on the package facade that re-exported it.
5. Run relevant pytest, lint, type and quality gates.

Verify: no `fastapi`, `api`, `sqlalchemy`, `telethon` or raw-provider imports (`tests/test_architecture.py` asserts the first four; `httpx` is convention only); public I/O is async and typed; HTTP concerns remain in `api/`.
