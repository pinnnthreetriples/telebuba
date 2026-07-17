---
last_updated: 2026-07-16
---

# Add API Endpoint
1. Add Pydantic request/response models; paginated lists use `Page[T]`.
2. Implement and test policy in `services/` through `core/` gateways.
3. Add an async `api/v1/` route: bind, authorize, call, return.
4. Use the shared error envelope and locale-neutral values.
5. Add route tests mocking the service.
6. Run `uv run python -m tools.gen_api` and relevant backend/frontend gates.

Verify: no business logic or direct DB/Telegram/provider imports in `api/`; protected routes use `get_current_user`; service/route tests pass; generated client is committed.
