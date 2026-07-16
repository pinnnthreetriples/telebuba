---
last_updated: 2026-07-16
---

# MEX Setup

The scaffold is already populated. Do not re-run setup during normal development.

## Requirements

- Node.js 20+ for stable MEX v0.6.3.
- Run commands from the repository root, not from inside `.mex/`.

## Install or refresh MEX

```bash
npx mex-agent setup --dry-run
npx mex-agent check
npx mex-agent doctor
```

`setup` preserves populated files, creates missing scaffold files, and refreshes tool configuration. Review its dry-run before applying it to an existing project.

## Normal operation

```bash
npx mex-agent check --quiet
npx mex-agent sync --dry-run
npx mex-agent sync
npx mex-agent timeline
```

Use `mex log --source agent --status implemented "<decision or durable fact>"` for history. Keep `.mex/AGENTS.md` small, current state in `ROUTER.md`, task knowledge in `context/`, and reusable procedures in `patterns/`.

Project environment setup is documented in `context/setup.md`.
