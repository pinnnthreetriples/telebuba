---
last_updated: 2026-07-16
---

# Backend Rules

1. `api/` validates, authorizes, calls a service, maps errors, and serializes—nothing else.
2. Business policy/state transitions live in `services/`; persistence and SDK access live in `core/`.
3. Cross-layer inputs/outputs are Pydantic models; collections use typed wrappers such as `Page[T]`.
4. DB uses repositories; Telegram uses typed gateway actions; AI/auth/logging/events use their `core/` gateways.
5. No `print()`, raw environment reads, operational magic values, or translated display text in backend responses.
6. Public I/O is async and typed; wrapped exceptions use `raise ... from e`.
7. Device fingerprints are immutable; secrets, `.session`, tdata and proxy passwords never enter logs or git.
8. Package roots stay thin; split by responsibility; test files stay at or below 700 lines.
9. Behavior changes include tests. Backend branch coverage is at least 90%; warnings, unknown markers and unexpected xpass fail.
10. Frontend rules live in `frontend.md`.

Files/functions/tables use `snake_case`; models use `PascalCase`; functions are verb-first. Run the relevant commands from `setup.md`; claim only checks actually executed.
