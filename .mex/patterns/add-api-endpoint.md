---
last_updated: 2026-08-04
---

# Add API Endpoint
1. Add Pydantic request/response models; paginated lists use `Page[T]`.
2. Implement and test policy in `services/` through `core/` gateways.
3. Add an async `api/v1/` route: bind, call, return. Do NOT attach an auth dependency — `api/v1/__init__.py` mounts every domain router behind `Depends(get_current_user)`; only `auth` and `health` are unprotected.
4. Set `operation_id` on the decorator, always. It is copied verbatim into the generated client (`operation_id="listProxies"` → `export const listProxies`), so omitting it lets hey-api synthesize a name from path+method and silently renames the TS surface. Only `include_in_schema=False` routes (binary thumbnails, SSE) skip it.
5. Use the shared error envelope and locale-neutral values.
6. When a router nears the file-size budget, move a cohesive cluster into a private sibling exporting a bare `APIRouter()`; the parent keeps prefix, tags and auth.
7. Add route tests mocking the service.
8. Run `uv run python -m tools.gen_api` and relevant backend/frontend gates.

Verify: no business logic or direct DB/Telegram/provider imports in `api/`; schema-visible routes carry stable `operation_id`s; service/route tests pass; regenerated client is committed.
