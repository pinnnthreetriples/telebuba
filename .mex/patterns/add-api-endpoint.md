---
name: add-api-endpoint
description: Add a backend /api/v1 endpoint as a UI-thin FastAPI route over a service, with schemas and tests.
triggers:
  - "add endpoint"
  - "new route"
  - "api/v1"
  - "fastapi route"
edges:
  - target: context/conventions.md
    condition: always — the api/ layer has hard structural rules
  - target: context/architecture.md
    condition: when deciding what belongs in core/ vs services/ vs api/
  - target: patterns/add-service.md
    condition: when the endpoint needs new business logic
  - target: patterns/add-telegram-task.md
    condition: when the endpoint performs Telegram I/O
last_updated: 2026-06-28
---

# Add an API Endpoint

## Context

Read `context/conventions.md` and `context/services.md` first. Key constraints:
- `api/` is UI-thin: a route validates input, calls a service, serializes the result.
- `api/` imports **only** `services/`, `schemas/`, `core.config`, `core.logging`, `fastapi`.
  Never `core.db` / `core.repositories` / `core.telegram_client` / `sqlalchemy` / `telethon`.
- All inputs/outputs crossing layers are Pydantic models in `schemas/`. Paginated lists use
  the generic `Page[T]` from `schemas/api.py`.
- Responses are locale-neutral: codes/enums + ISO-8601 timestamps, never display text.

## Steps

1. **Schema first.** Add or extend `schemas/<domain>.py` with request/response models. For a
   paginated list, return `Page[YourRead]`. Add new Telegram actions to
   `schemas/telegram_actions.py` only if needed.
2. **Service first.** Add or extend `services/<domain>.py` or `services/<domain>/` with the
   business logic. Public functions take/return Pydantic models and delegate I/O to `core/`.
3. **Test the service.** Add/update tests under `tests/services/`, mocking `core/` adapters.
   Prefer `/tdd`.
4. **Add the route.** In `api/v1/<domain>.py`, define the route on the domain `APIRouter`:
   bind the request model, call the service, return its Pydantic result (set `response_model`).
   Guard protected routes with `Depends(get_current_user)` from `api/deps.py`.
5. **Wire it in.** Make sure the domain router is included by the app factory in `api/__init__.py`
   under the `/api/v1` prefix (once per domain).
6. **Errors.** Let domain failures surface as the shared error envelope via `api/errors.py`
   (handlers map exceptions → `{error:{code,message,fields?}}`); 422 is already remapped.
7. **Test the route.** Add/update `tests/api/test_<domain>.py` with FastAPI's `TestClient`/
   `httpx.ASGITransport`: assert status, envelope shape, and that the service is called. Mock
   the service — do not re-test service logic here.
8. **Regenerate the client.** The endpoint changes the OpenAPI surface → run the gen-api script
   so `frontend/src/shared/api` matches, and commit it (CI drift-checks this — see `ci.md`).
9. **Run gates.** `uv run pytest`, `uv run ruff check .`, `uv run ty check .`, aislop gate.

## Gotchas

- Tempted to read a repository directly from the route for a "simple GET"? Don't — that is the
  exact drift the firewall caught (`features/neurocomment/_page.py` → `core.db`). Add a thin
  pass-through service; it is the accepted cost of the layer rule.
- Don't return bare `dict`/`list` — wrap in a Pydantic model (`Page[T]` for lists).
- Don't bake RU/EN text into a response — return a code/enum; the SPA localizes it.
- Keep the route async; the service boundary is `async def`.

## Verify

- [ ] Route is thin: validate → call service → serialize
- [ ] No business logic, no `core.db`/`core.repositories`/`core.telegram_client`/`sqlalchemy`/`telethon` in `api/`
- [ ] All cross-layer signatures use Pydantic models from `schemas/`; lists via `Page[T]`
- [ ] Response is locale-neutral (codes/enums + ISO timestamps)
- [ ] Protected routes use `Depends(get_current_user)`
- [ ] Service tests + route tests exist
- [ ] Generated client regenerated + committed
- [ ] `uv run pytest` passes, coverage ≥ 90%

## After

Run the GROW step from `ROUTER.md` (update `state/active.md`, touch any out-of-date `context/`,
bump `last_updated`).
</content>
