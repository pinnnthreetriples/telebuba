---
name: skills
description: Default-applied skills (ponytail, karpathy-guidelines) and project-local matt-pocock skills with telebuba-tailored triggers. Load when deciding whether a skill should be invoked.
triggers:
  - "skill"
  - "tdd"
  - "diagnose"
  - "prototype"
  - "grill"
  - "zoom out"
  - "to-prd"
  - "to-issues"
edges:
  - target: state/active.md
    condition: when the Project Skill Configuration changes (issue tracker, labels, board)
  - target: context/kanban.md
    condition: for board-related skills (to-prd, to-issues)
  - target: context/conventions.md
    condition: when /tdd is invoked — rule 7 sets the strict test policy
last_updated: 2026-06-16
---

# Skills

## Default Skills (apply silently — do not announce or re-invoke each turn)

Global skills the user has set as defaults. Every session applies them without being asked.

- **`karpathy-guidelines`** — coding behaviour: surface assumptions, prefer the minimum code, surgical changes only, transform tasks into verifiable goals. Applies to every write/review/refactor action.
- **`ponytail ultra`** — code minimisation: YAGNI extremist, deletion before addition, challenge the requirement before building. Active every response. `/ponytail-review` runs automatically after every code task. `/ponytail-audit` on whole-repo work. `/ponytail-debt` to harvest deferred shortcuts.

## Agent Skills (project-local — invoke on the listed trigger)

Installed in `.claude/skills/`. The agent does NOT auto-run them — invoke via the `Skill` tool when the trigger fires. Prefer the skill over reinventing the workflow.

### Engineering loops

- **`/tdd`** — red-green-refactor. **Trigger:** any new feature in `features/` (rule 7 already mandates a test); any bug fix where reproducing in a test is feasible; user says "let's TDD this" / "test-first". Aligns with our strict pytest config and 90 % branch-coverage floor.
- **`/diagnose`** — disciplined bug loop: reproduce → minimise → hypothesise → instrument → fix → regression-test. **Trigger:** user reports something broken / throwing / failing; an integration regresses; a Sentry alert lands. Always prefer this over freelancing a fix.
- **`/prototype`** — throwaway exploration. **Trigger:** before committing to a non-obvious data model, state machine, or UI option. Forbidden in `features/` / `core/` / `schemas/` — prototypes live outside the production tree.
- **`/improve-codebase-architecture`** — find deepening opportunities. **Trigger:** when `core/` or `features/` start showing duplication, shallow modules, or the non-negotiables in `context/conventions.md` are getting bent. Run it before a refactor, not after.

### Planning and scoping

- **`/grill-with-docs`** — stress-test a plan against `.mex/` context and decisions. **Trigger:** before a non-trivial change, especially anything that crosses layer boundaries or touches a non-negotiable. Catches drift early.
- **`/zoom-out`** — higher-level map of an unfamiliar area. **Trigger:** when the agent is about to act in a part of the codebase it has not loaded into context this session.

### Board / issue tracker

- **`/to-prd`** — turn the current conversation into a PRD on the issue tracker. **Trigger:** user says "this should be a PRD" or after a long discussion that decided non-trivial scope.
- **`/to-issues`** — split a plan / PRD into independently-grabbable board items (tracer-bullet vertical slices). **Trigger:** after a PRD lands, or whenever a single conversation has grown several actionable threads. Outputs go to `Backlog` per `context/kanban.md`.

### Setup / safety

- **`/setup-matt-pocock-skills`** — bootstraps an `## Agent skills` block in agent config files so the engineering skills know the project's issue tracker, triage labels, and domain doc layout. **Trigger:** if a matt-pocock skill complains it can't find that context. **Warning:** the skill writes its own block; our hand-written `Project Skill Configuration` below would be overwritten. Re-run only if we want a clean rewrite.
- **`/git-guardrails-claude-code`** — install Claude Code hooks that block destructive git ops (`push --force`, `reset --hard`, `clean -fd`, `branch -D`). **Trigger:** if anyone ever runs a destructive git command "by accident". Note: modifies the global Claude Code settings. Discuss with the user before invoking.

## Project Skill Configuration

This block is what `setup-matt-pocock-skills` would produce. Hand-written so the engineering skills above have the context they need.

- **Issue tracker:** GitHub Issues on the `telebuba` repo (owner: `pinnnthreetriples`). Use `gh` (or `D:/gh.exe` on Windows if `gh` is not in PATH).
- **Project board:** GitHub Project #2 (`telebuba`) — full protocol and column IDs in `context/kanban.md`. Status: `Backlog` → `Ready` → `In progress` → `In review` → `Done`.
- **Triage labels:** none yet. When the first label is created, add it here and to `state/active.md`.
- **Domain docs:** `.mex/ROUTER.md` is the entrypoint; `.mex/context/` and `.mex/patterns/` are the domain source of truth. `state/active.md` carries live state.
