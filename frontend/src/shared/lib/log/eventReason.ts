import type { TFunction } from 'i18next';

import type { LogEntry } from '@/shared/api';

// Where a label for `extra.error_type` may live. `accounts.*.code.*` are the gateway's
// own stable codes (`session_dead`, `chat_admin_required`) — gateway-wide vocabulary that
// sits under an `accounts` key only because the accounts page was the first screen to
// translate them, and the gateway now logs them here too. Copying those 36 strings into a
// log-private namespace would buy nothing but two wordings of the same refusal.
const ERROR_LABEL_PREFIXES = [
  'logEventTelegram.error',
  'accounts.profile.code',
  'accounts.channel.code',
];

// `extra` is a free-form object, hence the type check.
function extraStr(extra: LogEntry['extra'], key: string): string | undefined {
  const value = extra?.[key];
  return typeof value === 'string' ? value : undefined;
}

/**
 * Localize WHY a log row turned out the way it did — the half of a failure that the
 * event label alone never says. Built from two `extra` fields and joined with ' · ':
 *
 * 1. `extra.reason`, falling back to `extra.status`, through `logEventReason.*`.
 *    Most negative outcomes carry a `reason`; a failed post carries the Telegram `status`.
 * 2. `extra.error_type` — what Telegram refused, an exception class or a gateway stable
 *    code — through the `ERROR_LABEL_PREFIXES` ladder, raw value as the fallback.
 *
 * Empty string when the row carries neither, so a caller can render nothing (or its own
 * placeholder) instead of a stray separator.
 */
export function eventReason(t: TFunction, entry: LogEntry): string {
  const reasonCode = extraStr(entry.extra, 'reason') ?? extraStr(entry.extra, 'status');
  const reason = reasonCode ? t(`logEventReason.${reasonCode}`, { defaultValue: '' }) : '';
  // The error type is shown NEXT TO the reason rather than as a fallback behind it:
  // `status: "failed"` always translates, so a fallback would never fire and the line
  // would keep saying a post failed without saying why. Translated because this half IS
  // the answer to "what went wrong", and `ChannelPrivateError` next to the word "ошибка"
  // says nothing twice; anything none of the maps know still renders raw, like
  // eventLabel's raw-event-code fallback.
  const errorType = extraStr(entry.extra, 'error_type');
  // i18next resolves a key ARRAY to the first one that exists, so the ladder is its job,
  // not ours — and `defaultValue` then carries the raw value through untouched.
  const error = errorType
    ? t(
        ERROR_LABEL_PREFIXES.map((prefix) => `${prefix}.${errorType}`),
        { defaultValue: errorType },
      )
    : '';
  return [reason, error].filter(Boolean).join(' · ');
}
