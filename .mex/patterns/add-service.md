---
last_updated: 2026-08-04
---

# Add Service
1. Define cross-layer Pydantic contracts.
2. Add logic to the focused `services/` domain module; keep package facades re-export-only.
3. Delegate DB, Telegram, providers, config and logging to `core/` gateways. Route injectable collaborators through the domain seam when one exists.
4. Add tests mocking gateways and covering success/failure branches. Patch a collaborator on the module that owns its binding, not a re-exporting facade.
5. Run relevant pytest, lint, type and quality gates.

Verify against `tests/test_architecture.py`: public I/O is async and typed, persistence/SDK access stays in `core/`, and HTTP concerns remain in `api/`.
