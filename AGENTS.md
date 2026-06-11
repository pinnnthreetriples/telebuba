# Telebuba — Agent Instructions

> **Read [`.mex/AGENTS.md`](.mex/AGENTS.md) in full before doing any work, then
> follow the routing flow in [`.mex/ROUTER.md`](.mex/ROUTER.md).** Those files —
> and the `.mex/context/*` files they reference — are the single source of truth
> for this repository's architecture, conventions, and non-negotiables.

This file is the entry point for `AGENTS.md`-aware agents (OpenAI Codex, Cursor,
…). It is intentionally a thin pointer: the project keeps its canonical
instructions under `.mex/` (the `mex` scaffold), and each tool gets its own thin
entry point so the rules never drift between tools.

| Tool | Entry point | Mechanism |
| --- | --- | --- |
| OpenAI Codex / Cursor / other `AGENTS.md`-aware agents | `AGENTS.md` (this file) | the tool injects this file; you then open and follow `.mex/AGENTS.md` |
| Claude Code | `CLAUDE.md` | imports `@.mex/AGENTS.md` directly |

Why prose and not `@.mex/AGENTS.md` here: the `@import` syntax is a Claude Code
feature. Codex and most other agents read `AGENTS.md` as plain Markdown and do
**not** expand `@path` imports, so this file gives them an explicit, readable
instruction to open `.mex/AGENTS.md` instead.

**Do not add project rules to this file.** Edit `.mex/AGENTS.md` and the
`.mex/context/*` files — this entry point only routes agents to them.
