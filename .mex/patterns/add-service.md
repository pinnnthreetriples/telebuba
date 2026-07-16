---
last_updated: 2026-07-16
---

# Add Service
1. Define cross-layer Pydantic contracts.
2. Add logic to `services/<domain>.py` or a focused package submodule; keep `__init__.py` thin.
3. Delegate DB, Telegram, providers, config and logging to `core/` gateways.
4. Add tests mocking gateways and covering success/failure branches.
5. Run relevant pytest, lint, type and quality gates.

Verify: no FastAPI/UI/SQLAlchemy/Telethon/raw-provider imports; public I/O is async and typed; HTTP concerns remain in `api/`.
