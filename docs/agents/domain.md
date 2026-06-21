# Domain Docs

Where the engineering skills should look for this project's domain knowledge.

**This project does not use generic `CONTEXT.md` / `docs/adr` files.** Its domain source of truth is the **`.mex/` scaffold**:

- **`.mex/ROUTER.md`** — entrypoint + routing table; read first.
- **`.mex/AGENTS.md`** — always-loaded anchor (identity, stack, file map, non-negotiables).
- **`.mex/context/`** — architecture, conventions, stack, decisions, per-domain docs.
- **`.mex/state/active.md`** — live implementation state.
- **`.mex/patterns/`** — reusable task guides.

## Rules for skills

- Before exploring, read `.mex/ROUTER.md` and the relevant `.mex/context/` file (use its routing table).
- Use the vocabulary defined in `.mex/context/`; don't drift to synonyms.
- Architectural decisions live in `.mex/context/decisions.md`. If your output contradicts one, surface it explicitly rather than overriding silently.
