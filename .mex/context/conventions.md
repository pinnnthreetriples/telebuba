---
last_updated: 2026-08-04
---

# Backend Rules

1. `api/` validates, authorizes, calls a service, maps errors, and serializes—nothing else.
2. Business policy/state transitions live in `services/`; persistence and SDK access live in `core/`.
3. Cross-layer inputs/outputs are Pydantic models; collections use typed wrappers such as `Page[T]`.
4. DB uses repositories; Telegram uses typed gateway actions; AI/auth/logging/events use their `core/` gateways. Every `log_event` name carries its domain prefix (`warming_`, `neurocomment_`, …) — including from a shared `core/` helper only one domain reaches — because the per-domain feeds separate solely by `event LIKE 'prefix%'`; see `patterns/add-log-event.md`.
5. No `print()`, raw environment reads, operational magic values, or translated display text in backend responses. Every `core/config.py` field needs a matching `.env.example` key holding its in-code default.
6. Public I/O is async and typed; wrapped exceptions use `raise ... from e`.
7. Device fingerprints are immutable; secrets, `.session`, tdata and proxy passwords never enter logs or git.
8. Package roots stay thin; split by responsibility; test files stay at or below 700 lines — backend `tests/**` *and* frontend `*.test.*` / test-helper sources alike.
9. Behavior changes include tests. Backend branch coverage is at least 90%; warnings, unknown markers and unexpected xpass fail.
10. Frontend rules live in `frontend.md`.

`tests/test_api_error_contract.py` is the executable form of the error half of rule 1:
every documented operation declares exactly the non-2xx statuses the test *derives*
for it, each typed as `ErrorEnvelope`. The derived set is not "what the route can
answer" in the abstract — it is what is visible from `api/`: `raise HTTPException`
sites reached through `api/` helpers and the `Depends` chain, the statuses of the
handlers registered in `api/errors.py`, plus 422 wherever FastAPI's own rule
documents its auto validation response. That last carve-out is deliberate and does
over-declare: thirteen parameterless authenticated operations inherit a 422 they
cannot answer, because the session cookie is a route parameter. Mirroring FastAPI
beats fighting it — suppressing the auto-422 needs a `4XX`/`default` catch-all that
would blur every other status. The test's own three blind spots are documented in its
header (import style, the `services/` boundary, and treating entry into
`service_errors_to_http` as proof of the statuses it maps). Declare with
`api.errors.error_responses(*statuses)` or the named
compositions (`PROTECTED_ERRORS`, `SERVICE_ERRORS`) — never a hand-rolled
`{status: {...}}` dict, and put a fragment on a router only when *every* route under
it answers that set (`include_router` merges responses down and a route cannot
subtract; that is how the pre-#323 blanket came to advertise 404/503 on
`GET /accounts/stats`).

`tests/test_architecture.py` is the executable form of rules 1–5 and 8, and outranks this prose. It `rglob`s each layer (submodules included) and asserts: `api/` imports only `services`, `schemas`, `fastapi`, stdlib and `core.config` / `core.logging` — nothing else from `core/`; `services/` imports no `sqlalchemy`, `telethon`, `fastapi`, `api` (no `httpx` either, but that one is convention, not asserted); `schemas/` imports no project layer and no SDK; `core/` imports neither layer above it; `.env.example` mirrors `core/config.py` key-for-key *and* value-for-value; no test source exceeds 700 lines.

Numbering caveat: ~50 docstrings cite `non-negotiable #N`, and those numbers index the pre-#257 rule list, not this compacted one. That list ran: 1 api-thin, 2 Pydantic boundaries, 3 no hardcoded values, 4 logging-only, 5 layer isolation, 6 DB/Telegram gateways, 7 test coverage, 8 async & typing, 9 fingerprint immutable, 10 configuration-driven, 11 services layer — plus a `#12` the code coined for locale-neutral response codes, which was only ever a checklist bullet. Resolve a citation against *that* list (code's `#5` is layer isolation, not rule 5 here) and leave the numbers alone; renumbering 50 docstrings to a list that will compact again is not worth it.

Files/functions/tables use `snake_case`; models use `PascalCase`; functions are verb-first. Run the relevant commands from `setup.md`; claim only checks actually executed.
