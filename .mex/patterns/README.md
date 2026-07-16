---
last_updated: 2026-07-16
---

# Patterns
Patterns are short project-specific runbooks for recurring or dangerous tasks. Check `INDEX.md` before implementation.

Create or update a pattern only when it records a non-obvious workflow, gotcha, or verification step not already enforced by code/tests/context. Do not duplicate general conventions or create speculative patterns.

## Minimal format
```markdown
---
name: task-name
description: When to use this runbook.
triggers: [keyword]
edges:
  - target: context/relevant.md
    condition: required context
last_updated: YYYY-MM-DD
---
# Task
## Steps
1. ...
## Gotchas
- ...
## Verify
- ...
```

Keep each runbook focused, use real paths/commands, and update `INDEX.md`. After work, change `ROUTER.md` only if current project state changed; use `mex log` for durable rationale.