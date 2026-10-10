# Coding Standards

Read before writing or reviewing backend code. Frontend rules live in `.mex/context/frontend.md`. Executable checks outrank this prose: `tests/test_architecture.py` is the layer, config and file-size rules as tests.

## Layers
- `api/` validates/authorizes, calls services, maps errors and serializes; policy stays out.
- Business policy/state transitions live in `services/`; persistence and SDK access live in `core/`.
- Cross-layer inputs/outputs are Pydantic models; collections use typed wrappers such as `Page[T]`.
- DB uses repositories; Telegram/providers/logging/events use `core/` gateways. Injectable domain collaborators go through focused seams.

## Code
- No `print()`, raw environment reads, operational magic values, or translated display text in backend responses. `core/config.py` and `.env.example` stay key/value aligned.
- Public I/O is async and typed; wrapped exceptions use `raise ... from e`.
- Package roots stay thin; split by responsibility. Backend and frontend test/test-helper sources stay ≤700 lines.
- Behavior changes include tests. Backend branch coverage stays ≥90%; warnings, unknown markers and unexpected xpass fail.
- Files/functions/tables use `snake_case`; models use `PascalCase`; functions are verb-first.

## API errors
`tests/test_api_error_contract.py` derives each operation's visible non-2xx statuses from `api/` code/dependencies plus registered handlers and checks the OpenAPI `ErrorEnvelope`. Use `api.errors.error_responses(...)` or its named compositions; read that test's header for its deliberate analysis limits instead of duplicating them here.

## Legacy citations
Comments that say `non-negotiable #N` refer to an older checklist and must not be mapped onto any list here. When touching one, prefer a named invariant or executable-test reference; do not add new numeric citations.
