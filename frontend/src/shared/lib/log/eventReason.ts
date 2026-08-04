import type { TFunction } from 'i18next';

import type { LogEntry } from '@/shared/api';

// Log-shaped copy, tried first. Five codes (`failed`, `flood_wait`, `slow_mode_wait`,
// `premium_wait`, `peer_flood`) live in BOTH `logEventReason` and `accounts.profile.code`
// in a different register, so before this order the operator's wording depended on which
// `extra` field the gateway happened to fill: "Telegram отклонил" from a `status`,
// "Telegram отклонил действие — попробуйте ещё раз" from an `error_type`.
const LOG_LABEL_PREFIXES = ['logEventReason', 'logEventTelegram.error'];
// The gateway's own stable codes (`session_dead`, `chat_admin_required`,
// `story_image_invalid`) — gateway-wide vocabulary that sits under an `accounts` key only
// because the accounts page was the first screen to translate them, and the gateway now
// logs them too. Copying those 46 strings into a log-private namespace would buy nothing
// but two wordings of the same refusal. Same order as the mutation toast walks them.
const TOAST_LABEL_PREFIXES = [
  'accounts.profile.code',
  'accounts.channel.code',
  'accounts.addStory.code',
];
// Those strings are shaped "what happened — what to do", written for a toast shown the
// instant the operator clicked. A log row is a historical record, so the imperative tail
// is nonsense there — "обновите список" about an event from Tuesday, "проверьте
// выделенные поля" with no form on screen. Cutting at the separator keeps ONE wording
// that cannot drift from the toast, and drops the tail's `{{s}}` placeholder, which a log
// row has no seconds to interpolate.
const TOAST_TAIL = ' — ';
// `extra.status` is an outcome enum, not an explanation, and two of its values are not
// failures at all (the gateway's own `_NON_FAILURE_STATUSES`). A warming cycle that ended
// well writes `status: "ok"`, so with the raw fallback below it would gain a "· ok" tail
// where it used to stay silent — silent only because an unmapped value rendered as
// nothing, which is the bug this file just stopped relying on.
const NON_FAILURE_STATUSES = ['ok', 'already_participant'];

// `extra` is a free-form object, hence the type check.
function extraStr(extra: LogEntry['extra'], key: string): string | undefined {
  const value = extra?.[key];
  return typeof value === 'string' ? value : undefined;
}

// i18next resolves a key ARRAY to the first one that exists, so the ladder is its job,
// not ours — and `defaultValue` then carries the raw value through untouched.
// `nsSeparator: false` because a value can legitimately contain a colon: the gateway
// builds `RPC: ChannelPrivateError` and `unavailable: TelegramClientPoolError`, and
// i18next would otherwise read everything before the colon as a namespace name, leaving
// such a key unreachable even once it has copy. `keySeparator` stays '.' — the prefixes
// ARE dotted paths — and a dot inside a value can only miss, which now renders raw.
function label(t: TFunction, code: string): string {
  const own = t(
    LOG_LABEL_PREFIXES.map((prefix) => `${prefix}.${code}`),
    { defaultValue: '', nsSeparator: false },
  );
  if (own) return own;
  const toast = t(
    TOAST_LABEL_PREFIXES.map((prefix) => `${prefix}.${code}`),
    { defaultValue: code, nsSeparator: false },
  );
  const tail = toast.indexOf(TOAST_TAIL);
  return tail === -1 ? toast : toast.slice(0, tail);
}

/**
 * Localize WHY a log row turned out the way it did — the half of a failure that the
 * event label alone never says. Built from two `extra` fields and joined with ' · ':
 *
 * 1. `extra.reason`, falling back to a FAILING `extra.status`. Most negative outcomes
 *    carry a `reason`; a failed post carries the Telegram `status`.
 * 2. `extra.error_type` — what Telegram refused, an exception class or a gateway stable
 *    code.
 *
 * Both halves go through the SAME ladder and the same raw fallback. `reason` used to be
 * resolved through `logEventReason` alone with an empty default, so anything unmapped
 * rendered as NOTHING: the sweep's `TelegramReadError` reason — `FloodWait(120s)`,
 * `RPC: ChannelPrivateError` — vanished, which is the very thing it was added to say, and
 * a failed discovery run (a bare class name in `reason`, no `error_type`) showed a blank.
 *
 * The error type is shown NEXT TO the reason rather than as a fallback behind it:
 * `status: "failed"` always translates, so a fallback would never fire and the line would
 * keep saying a post failed without saying why.
 *
 * Empty string when the row carries neither, so a caller can render nothing (or its own
 * placeholder) instead of a stray separator.
 */
export function eventReason(t: TFunction, entry: LogEntry): string {
  const status = extraStr(entry.extra, 'status');
  const reasonCode =
    extraStr(entry.extra, 'reason') ??
    (status && !NON_FAILURE_STATUSES.includes(status) ? status : undefined);
  const errorType = extraStr(entry.extra, 'error_type');
  return [reasonCode, errorType]
    .filter((code): code is string => Boolean(code))
    .map((code) => label(t, code))
    .join(' · ');
}
