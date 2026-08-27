---
name: router
description: Minimal task router for Telebuba project memory.
edges:
  - target: context/architecture.md
    condition: backend flow, stack, services, gateways, or system design
  - target: context/conventions.md
    condition: backend implementation or review
  - target: context/frontend.md
    condition: React, FSD, TypeScript, i18n, design tokens, or frontend gates
  - target: context/runtime-telegram.md
    condition: Telegram client, sessions, proxies, profile, privacy, or account removal
  - target: context/runtime-warming.md
    condition: warming scheduling, budget, dialogue, recovery, or board runtime
  - target: context/runtime-neurocomment.md
    condition: neurocomment listener, comments, captcha, joins, cooldowns, or retention
  - target: context/runtime-discovery.md
    condition: neurocomment campaign channel discovery
  - target: context/runtime-neuroshilling.md
    condition: neuroshilling campaigns, scenarios, dialogue runs, or chat revival
  - target: context/setup.md
    condition: setup, commands, CI, hooks, Windows checkout, or verification
  - target: patterns/INDEX.md
    condition: repeatable implementation task
last_updated: 2026-08-27
---

# Telebuba Router

Use only the matching frontmatter route. Load `patterns/INDEX.md` only for a repeatable implementation task, then one matching pattern. For rationale/history use a small `mex timeline` query, Git, or merged PRs instead of putting history back into routed memory.

Trust code, tests, manifests, migrations and workflows over prose memory.

## Workflow
1. Load the matching context and, only if useful, one pattern.
2. Implement through the documented boundaries.
3. Verify with commands from `context/setup.md`; report only checks actually run.
4. If durable truth changed, update only its route; use `mex log` for rationale and a pattern only for recurring work.
