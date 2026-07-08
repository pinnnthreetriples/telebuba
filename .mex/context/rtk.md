---
name: rtk
description: RTK (Rust Token Killer) shell-command wrapper policy. Load when running a Bash command that touches the categories below, or when the global hook is not active.
triggers:
  - "rtk"
  - "token killer"
  - "token saving"
  - "bash wrapper"
edges:
  - target: context/setup.md
    condition: when listing developer commands
  - target: state/active.md
    condition: when a known issue mentions rtk hook unavailability
last_updated: 2026-07-06
---

# RTK (Rust Token Killer)

A `PreToolUse` hook on `Bash` rewrites commands to `rtk <cmd>` automatically — agents never type `rtk` by hand. The hook is in the global Claude Code settings; if it is not active in a session, prefix commands manually for the categories below. Goal: 60–90 % fewer tokens spent on verbose shell output.

## Prefix `rtk` for these categories

- **Tests:** `rtk uv run pytest`, `rtk uv run pytest -k <name>`.
- **Linters / type checkers:** `rtk uv run ruff check .`, `rtk uv run ty check .`, `rtk uv run bandit -r .`.
- **Git inspection:** `rtk git diff`, `rtk git log`, `rtk git status`.
- **Shell file reads:** `rtk cat <file>` (only when the dedicated `Read` tool is not appropriate).

## Do NOT prefix `rtk` for

- Interactive commands or commands that need a TTY (`uv run python` `main.py` while developing, `gh auth login`, anything with REPL).
- Commands where the user explicitly asks for the raw output.
- One-shot operations whose output is already terse (e.g. `git push`, `git commit`).

## Meta commands (always typed directly)

- `rtk gain` — token-savings analytics for the session.
- `rtk discover` — analyse history for missed wrapping opportunities.
- `rtk proxy <cmd>` — run without filtering when you need exact upstream output.

Full reference: the global RTK configuration file in the Claude user config directory.
