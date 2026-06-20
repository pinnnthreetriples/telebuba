@.mex/AGENTS.md

## Claude Code — session rules

- At the start of every session run `npx mex-agent check --quiet` and report the drift score.
- If drift errors are found, run `npx mex-agent sync --dry-run` before writing any code.
- Always start tasks in Plan Mode (`/plan`) before switching to execution.
- After meaningful work, run the GROW step: update `state/active.md`, bump `last_updated`, move board item.
