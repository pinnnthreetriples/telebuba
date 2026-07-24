---
last_updated: 2026-07-24
---

# Frontend Rules
React 19 + strict TypeScript + Vite. Server I/O uses the generated `shared/api` client with TanStack Query; never call URLs directly or hand-edit generated files.

FSD order: `app → routes → pages → widgets → features → entities → shared`. Import only lower layers and cross slice boundaries through `index.ts`.

- Routes/pages compose; features own interactions; entities own business nouns; `shared` owns generic API/UI/lib/config/i18n.
- No `any` or ignored type failures without a precise upstream justification.
- All display strings and formatting use react-i18next/`Intl` (`ru`, `en`).
- Reuse Tailwind tokens and local `shared/ui`; do not duplicate backend policy.
- Account display identity is per-surface: reuse `entities/account` helpers (`accountDisplayName`, `accountInitials`, `<AccountAvatar>`); any account-bearing payload (e.g. warming cards) must carry `first_name`/`last_name`/`phone`/`avatar_etag`, not just a label.
- Frontend configuration uses `VITE_*`.
- Vitest logic coverage stays at least 80%; Steiger, ESLint, Prettier, TypeScript, tests and build must pass.

Run frontend gates from `context/setup.md`; `frontend/package.json` and boundary tests are the executable source of truth.
