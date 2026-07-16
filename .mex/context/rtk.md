---
name: rtk
description: Optional shell-output compression policy.
triggers: [rtk, shell output, token saving]
edges:
  - target: context/setup.md
    condition: developer command
  - target: context/ci.md
    condition: verbose gate output
last_updated: 2026-07-16
---

# RTK
The global Bash hook may wrap verbose commands automatically. When absent, prefix `rtk` for tests, lint/type checks, and git inspection. Do not use it for interactive/TTY commands, raw-output requests, or already-terse write operations. Never depend on RTK for command correctness.