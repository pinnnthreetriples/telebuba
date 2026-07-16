---
name: add-api-endpoint
description: Add a thin /api/v1 route over a service.
triggers: [add endpoint, new route, api/v1]
edges:
  - target: context/conventions.md
    condition: backend rules
  - target: patterns/add-service.md
    condition: new business logic
last_updated: 2026-07-16
---

# Add API Endpoint

## Steps
1. Add Pydantic request/response models in `schemas/`; paginated lists use `Page[T]`.
2. Implement and test business behavior in `services/` using `core/` gateways.
3. Add the async route in `api/v1/`: bind, authorize, call service, return model.
4. Map domain failures through the shared error envelope; keep responses locale-neutral.
5. Add route tests that mock the service.
6. Regenerate the frontend API client with `uv run python -m tools.gen_api`.
7. Run relevant backend and frontend gates.

## Verify
No business logic or direct DB/Telegram/provider imports in `api/`; cross-layer values are typed; protected routes use `get_current_user`; service and route tests pass; generated client is committed.