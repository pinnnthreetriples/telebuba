---
name: skills
description: Project skill triggers.
triggers: [skill, tdd, diagnose, prototype, grill, prd, issues]
edges:
  - target: context/conventions.md
    condition: engineering skill
  - target: context/kanban.md
    condition: issue or board skill
last_updated: 2026-07-16
---

# Skills
Use project skills from `.claude/skills/`; `.agents/skills/` is a synchronized copy.

- `/tdd`: new behavior or reproducible bug fix.
- `/diagnose`: failure reproduction and root-cause work.
- `/prototype`: disposable exploration outside production trees.
- `/improve-codebase-architecture`: before structural refactors.
- `/grill-with-docs`: challenge a non-trivial/cross-layer plan.
- `/zoom-out`: map an unfamiliar area before editing.
- `/to-prd`: turn decided scope into a GitHub issue PRD.
- `/to-issues`: split a plan into independent board items.
- `/triage`: explicit issue/PR triage only.

Issue tracker: `pinnnthreetriples/telebuba`; board: project #2; triage labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. Prefer the skill workflow over recreating it manually.