# PROTOTYPE — Масстегинг (delete when a variant wins)

**Question:** how should the Масстегинг page (story mentions + priming) look in Telebuba?
**Where:** `/prototype/masstagging?variant=A|B|C` (dev server only, mock data, no API, no session gate).
Arrows in the bottom bar (or ← →) switch variants; the draft is shared, so typed values survive a switch.

| Key | Layout | How priming is shown |
| --- | --- | --- |
| A | Reference (GramGPT) layout: dual-list account picker, two columns of collapsible cards | «Только прайминг» switch in «Настройки сторис»; story fields go grey |
| B | Neuroshilling shape: 328px sidebar (mode, what the target sees, launch) + one settings card | Mode picked first as two cards; story-only sections disappear |
| C | Six-step wizard + sticky summary | Mode is step 1; the «Креатив» step is skipped |
| D (default) | Chat broadcast / neuroshilling shape: campaigns sidebar, PipelineCard, targets board, log; settings in a dialog | Mode is section 1 of the dialog; the «Сторис» section disappears |

In D the sidebar campaigns cover every state: draft/ready (the live one, wired to the draft), paused, resting until tomorrow, stopped on flood limits, done. The account picker uses the real fleet (read-only). Competitor research: `RESEARCH.md`.

## Domain facts the UI is built on (Tavily research, 2026-10)
- **Story mentions:** the account posts a story from the media pool, and its caption @mentions N targets → the target gets a "mentioned you in a story" push.
- **Priming** (GramGPT term): no story. In a DM with the target, the account turns the auto-delete timer on and then off → the target sees "X включил(а) автоудаление" and opens the profile. The creative lives in the profile (pinned channel or a profile story).
- **Telegram limits** (limits.tginfo.me):
  - Free account: 1 story a day, only a few stories over the account's lifetime, no link stickers, 200-char caption, 24h lifetime.
  - Premium: up to 100 stories a day, 6/12/24/48h lifetime, up to 3 link stickers.
  - Username resolves: 200 a day per account.
  - So story mentions are effectively Premium-only, while priming works on any account.
- **Starting limits** (GramGPT guidance):
  - Delay before each target: 20–60 s, or 200–600 s in the first days.
  - Flood-limit pause: 500 s; stop the account after 3 flood limits.
  - Priming: 5–6 dialogs a day at first, 35–40 later.

## Backend sketch (after the visual is picked)
- `accounts.premium` already exists, so it can drive the badges and estimates.
- Story publishing already exists in `core/telegram_client/_media_stories.py` (`CanSendStory` / `SendStory`). Priming needs a new gateway call: `messages.SetHistoryTTL(peer, 86400)`, then `SetHistoryTTL(peer, 0)`.
- New `masstagging` slice, shaped like neuroshilling:
  - campaign (mode + settings);
  - per-target rows: `pending / tagged / skipped / failed`, plus the account that did it;
  - a global "already tagged" set for «Пропускать уже отмеченных».
- Runtime: one asyncio task per account (like warming), randomized delays, and daily counters per account (stories, dialogs, resolves).
- Error handling:
  - FloodWait → pause, then quarantine after N in a row.
  - Privacy errors → that target fails.
  - PeerFlood → spamblock.
- Account busy-locks are shared with warming, neurocomment and neuroshilling.
- Log codes are prefixed `masstagging_*`, and the page shows them in a LogTerminal.

## Verdict
_TBD by the operator: which variant, or which parts of each._
