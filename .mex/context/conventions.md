---
last_updated: 2026-08-21
edges:
  - target: context/architecture.md
    condition: layer boundaries, gateways or system design
  - target: patterns/INDEX.md
    condition: the change is a repeatable implementation task
---

# Backend Rules

1. `api/` validates/authorizes, calls services, maps errors and serializes; policy stays out.
2. Business policy/state transitions live in `services/`; persistence and SDK access live in `core/`.
3. Cross-layer inputs/outputs are Pydantic models; collections use typed wrappers such as `Page[T]`.
4. DB uses repositories; Telegram/providers/logging/events use `core/` gateways. Injectable domain collaborators go through focused seams.
5. No `print()`, raw environment reads, operational magic values, or translated display text in backend responses. `core/config.py` and `.env.example` stay key/value aligned.
6. Public I/O is async and typed; wrapped exceptions use `raise ... from e`.
7. A fingerprint's device identity (platform, model, OS, app version) is immutable; its language may be corrected exactly once, while still the fallback, before the account has acted. Credentials, `.session`, tdata and proxy passwords never enter logs or git.
8. Package roots stay thin; split by responsibility. Backend and frontend test/test-helper sources stay ≤700 lines.
9. Behavior changes include tests. Backend branch coverage stays ≥90%; warnings, unknown markers and unexpected xpass fail.

`tests/test_architecture.py` is the executable form of the layer/config/file-size rules and outranks this prose.

`tests/test_api_error_contract.py` derives each operation's visible non-2xx statuses from `api/` code/dependencies plus registered handlers and checks the OpenAPI `ErrorEnvelope`. Use `api.errors.error_responses(...)` or its named compositions; read that test's header for its deliberate analysis limits instead of duplicating them here.

Legacy comments that say `non-negotiable #N` refer to an older checklist and must not be mapped onto the numbered list above. When touching one, prefer a named invariant or executable-test reference; do not add new numeric citations.

Files/functions/tables use `snake_case`; models use `PascalCase`; functions are verb-first. Run only relevant checks from `setup.md` and report only what ran.
