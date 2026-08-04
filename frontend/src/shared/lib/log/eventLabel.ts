import type { TFunction } from 'i18next';

// The Telegram gateway logs action outcomes with dynamically composed codes:
// `telegram_{action}` (ok), `telegram_{action}_failed`, the flood family
// `telegram_{action}_{flood_wait|slow_mode_wait|premium_wait|peer_flood}`, and
// `telegram_{action}_already_participant` (see core/telegram_client/_actions.py).
// Enumerating every action×status combo as a flat key is unmaintainable, so those
// are labelled compositionally from `logEventTelegram.action.*` + `.status.*`.
const TG_STATUS_SUFFIXES = [
  'failed',
  'flood_wait',
  'slow_mode_wait',
  'premium_wait',
  'peer_flood',
  'already_participant',
] as const;

// The gateway also stamps the calling domain onto those names
// (`warming_telegram_join_channel`, `neurocomment_telegram_comment_on_post`) so each
// feed's `event_prefix` filter sees its own gateway rows — see `event_name` in
// core/telegram_client/_util.py. The label is domain-independent: the feed you are
// reading already tells you the domain, so strip the prefix and label the bare form.
// Lazy + `_`-tolerant so a multi-word domain (`spam_status_telegram_*`) strips too — the
// convention in .mex/patterns/add-log-event.md puts no shape constraint on a domain name.
const TG_DOMAIN_PREFIX = /^[a-z0-9_]+?_(?=telegram_)/;

function telegramLabel(t: TFunction, code: string): string {
  const body = code.slice('telegram_'.length);
  for (const status of TG_STATUS_SUFFIXES) {
    if (!body.endsWith(`_${status}`)) continue;
    const action = body.slice(0, -(status.length + 1));
    const actionLabel = t(`logEventTelegram.action.${action}`, { defaultValue: '' });
    if (!actionLabel) return '';
    const statusLabel = t(`logEventTelegram.status.${status}`, { defaultValue: '' });
    return statusLabel ? `${actionLabel} — ${statusLabel}` : actionLabel;
  }
  return t(`logEventTelegram.action.${body}`, { defaultValue: '' });
}

/**
 * Localize a backend log event code. The API is locale-neutral — it emits stable
 * snake_case codes and the SPA owns the labels. Resolution order:
 *
 * 1. An exact `logEvent.<code>` entry (the curated, single-source dictionary).
 * 2. An exact entry for the same code with its `<domain>_` gateway prefix stripped —
 *    the normal path for a gateway row, and after (1) so a domain-specific override key
 *    can win when someone adds one.
 * 3. For `telegram_*` action codes, a compositional label built from the action
 *    stem + status suffix (covers the whole dynamic action×status family).
 * 4. Otherwise the raw code — never a blank cell or a `logEvent.foo` placeholder.
 *
 * A CI parity test (`tests/test_logevent_i18n_parity.py`) fails the build when a
 * backend `log_event` code (literal or composed) lacks a translation, so the raw
 * fallback is a safety net rather than the normal path.
 */
export function eventLabel(t: TFunction, code: string): string {
  // A code that is already bare is never stripped: without this guard the lazy prefix
  // match would eat the first half of a hypothetical `telegram_relay_telegram_join_channel`
  // and mislabel it as a plain `telegram_join_channel`.
  const bare = code.startsWith('telegram_') ? code : code.replace(TG_DOMAIN_PREFIX, '');
  const exact =
    t(`logEvent.${code}`, { defaultValue: '' }) || t(`logEvent.${bare}`, { defaultValue: '' });
  if (exact) return exact;
  if (bare.startsWith('telegram_')) {
    const composed = telegramLabel(t, bare);
    if (composed) return composed;
  }
  return code;
}
