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
    condition: when touching Telethon or any Telegram interaction
  - target: context/warming.md
    condition: when working with scheduled warming jobs or APScheduler
  - target: context/logging.md
    condition: when emitting, querying, or displaying log entries
  - target: context/kanban.md
    condition: always at session start — protocol for the GitHub Project board (pick work, move issues)
  - target: context/skills.md
    condition: when deciding whether to invoke a skill (default or matt-pocock)
  - target: context/rtk.md
    condition: when running a shell command and the global rtk hook is uncertain
  - target: context/ci.md
    condition: when modifying workflows, debugging red CI, or planning a heavy check
  - target: patterns/INDEX.md
    condition: when starting a task — check the pattern index for a matching pattern
last_updated: 2026-06-10
---

# Session Bootstrap

`AGENTS.md` is always loaded (via root `CLAUDE.md` → `@.mex/AGENTS.md`). After this file, load `state/active.md` for the live picture (working / not built / known issues / open decisions) and `context/kanban.md` for the board protocol.

## Routing Table

| Task type | Load |
|-----------|------|
| Current project state (always first) | `state/active.md` |
| Picking work / moving board items (always at session start) | `context/kanban.md` |
| Understanding how the system works | `context/architecture.md` |
| Working with a specific technology | `context/stack.md` |
| Writing or reviewing code | `context/conventions.md` |
| Writing or reviewing business logic | `context/services.md` |
| Making a design decision | `context/decisions.md` |
| Setting up or running the project | `context/setup.md` |
| Anything Telethon / Telegram-related | `context/telegram.md` |
| Anything scheduled / warming-related | `context/warming.md` |
| Anything log-related (file / SQLite table / Logs page) | `context/logging.md` |
| Deciding whether / when to invoke a skill | `context/skills.md` |
| Modifying workflows / debugging CI | `context/ci.md` |
| Shell command policy (rtk wrapper) | `context/rtk.md` |
| Any specific task | check `patterns/INDEX.md` for a matching pattern |

## Methodology — Vertical Slice + Hexagonal (4 layers)

Every code change MUST land in the right layer. Before writing a single line, the agent declares which layer is changing and why.

```
features/    UI-thin handlers (NiceGUI). Validate → call service → render. Max ~5 lines per handler.
services/    Business logic. Async, Pydantic at edges, no SDK imports, no UI. Composes other services.
core/        Infrastructure (db, telegram_client, config, logging). Only place sqlalchemy/telethon/loguru live.
schemas/     Pure Pydantic types. No project imports. The data contract.
```

**Hard layer check before writing code (mandatory):**
1. Where does this code belong? (One of: features / services / core / schemas.) Name the file path.
2. What does it import? Cross-check against `context/architecture.md` Allowed/Forbidden Imports.
3. Is the input/output a Pydantic model from `schemas/`? If not, fix that first.
4. Would the same logic be called from a scheduler / CLI / test? If yes, it MUST be in `services/`, not `features/`.

If any check fails, STOP and re-plan. Do not "just write it and refactor later" — that is how the layers rot.

## Behavioural Contract

**Session start (run once before any task):** load `state/active.md` and `context/kanban.md`. Report what is in `In progress` and `Ready` on the board. Pick work per the kanban protocol.

For every task, follow this loop:

1. **CONTEXT** — Move the board item to `In progress` (kanban Step 3). Load the relevant context file(s) from the routing table above. **Always include `context/services.md` if the task involves any business logic.** Check `patterns/INDEX.md` for a matching pattern. If one exists, follow it. Narrate what you load: "Loading architecture context..."
2. **BUILD** — Run the **Hard layer check** above. State which layer the code belongs to and what it imports. THEN write the code. If you are about to deviate from an established pattern or layer rule, say so before writing any code — state the deviation and why.
3. **VERIFY** — Load `context/conventions.md` and run the Verify Checklist item by item. State each item and whether the output passes. Do not summarise — enumerate explicitly.
4. **DEBUG** — If verification fails or something breaks, check `patterns/INDEX.md` for a debug pattern. Follow it. Fix the issue and re-run VERIFY.
5. **GROW** — After meaningful work, run this binary checklist:
   - **Ground:** What changed in reality? Name the changed behavior, system, command, dependency, or workflow.
   - **Record:** Update `state/active.md` (Working / Not Yet Built / Known Issues). Update any `context/` file whose facts changed.
   - **Orient:** If this task can recur and no pattern exists, create one in `patterns/` using `patterns/README.md`, then add it to `patterns/INDEX.md`.
   - **Write:** Bump `last_updated` in every scaffold file you changed. Run `mex log --type decision "..."` when the why matters.
   - **Board:** Move the issue to `In review` when the PR is open, and to `Done` after the merge. See `context/kanban.md` for the exact commands.
