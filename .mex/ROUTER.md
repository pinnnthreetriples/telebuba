---
name: router
description: Session bootstrap and navigation hub. Stable — does not track live state. Read at the start of every session before any task.
edges:
  - target: state/active.md
    condition: always — load current project state first
  - target: context/architecture.md
    condition: when working on system design, integrations, or layer rules
  - target: context/stack.md
    condition: when working with specific technologies or making tech decisions
  - target: context/conventions.md
    condition: when writing or reviewing code
  - target: context/services.md
    condition: when writing or reviewing any business logic — services/ is where it lives
  - target: context/decisions.md
    condition: when making an architectural choice or understanding why something is built a certain way
  - target: context/setup.md
    condition: when setting up the dev environment or running the project
  - target: context/telegram.md
    condition: when touching the platform gateway
  - target: context/warming.md
    condition: when working with warming/runtime workflows
  - target: context/logging.md
    condition: when emitting, querying, or displaying log entries
  - target: context/kanban.md
    condition: always at session start — protocol for the GitHub Project board
  - target: context/skills.md
    condition: when deciding whether to invoke a skill
  - target: context/rtk.md
    condition: when running a shell command and the global rtk hook is uncertain
  - target: context/ci.md
    condition: when modifying workflows, debugging red CI, or planning a heavy check
  - target: patterns/INDEX.md
    condition: when starting a task — check the pattern index for a matching pattern
last_updated: 2026-06-16
---

# Session Bootstrap

`AGENTS.md` is always loaded (via root `CLAUDE.md` → `@.mex/AGENTS.md`). After this file, load `state/active.md` for the live picture and `context/kanban.md` for the board protocol.

## Routing Table

| Task type | Load |
|-----------|------|
| Current project state (always first) | `state/active.md` |
| Picking work / moving board items | `context/kanban.md` |
| Understanding how the system works | `context/architecture.md` |
| Working with a specific technology | `context/stack.md` |
| Writing or reviewing code | `context/conventions.md` |
| Writing or reviewing business logic | `context/services.md` |
| Making a design decision | `context/decisions.md` |
| Setting up or running the project | `context/setup.md` |
| Anything platform-gateway related | `context/telegram.md` |
| Anything warming/runtime-related | `context/warming.md` |
| Anything log-related | `context/logging.md` |
| Skills | `context/skills.md` |
| CI/workflows | `context/ci.md` |
| Shell command policy | `context/rtk.md` |
| Any specific task | check `patterns/INDEX.md` |

## Methodology — Vertical Slice + Hexagonal-lite (4 layers)

Every code change MUST land in the right layer. Before writing code, the agent declares which layer is changing and why.

```text
features/    UI-thin pages/components/handlers. Validate → call service → render.
services/    Business logic. Async, Pydantic at edges, no SDK imports, no UI.
core/        Infrastructure gateways: db/repositories, client gateways, config, logging.
schemas/     Pure Pydantic types. No project imports. The data contract.
```

**Hard layer check before writing code:**
1. Where does this code belong? Name the layer and file path.
2. What does it import? Cross-check `context/architecture.md`.
3. Is cross-layer input/output a Pydantic model from `schemas/`?
4. Would the same logic be called from a runtime task, CLI, or test? If yes, it belongs in `services/`, not `features/`.

If any check fails, stop and re-plan.

## Behavioural Contract

1. **CONTEXT** — Move board item to `In progress` when applicable. Load relevant context files. Always include `context/services.md` for business logic. Check `patterns/INDEX.md`.
2. **BUILD** — Run the hard layer check, then write code.
3. **VERIFY** — Load `context/conventions.md` and run the checklist item by item.
4. **DEBUG** — If verification fails, follow a matching debug pattern if one exists.
5. **GROW** — Record reality in `state/active.md`, update stale context/pattern files, bump `last_updated`, and move the board item when applicable.
