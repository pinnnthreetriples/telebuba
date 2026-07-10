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
 * 2. For `telegram_*` action codes, a compositional label built from the action
 *    stem + status suffix (covers the whole dynamic action×status family).
 * 3. Otherwise the raw code — never a blank cell or a `logEvent.foo` placeholder.
 *
 * A CI parity test (`tests/test_logevent_i18n_parity.py`) fails the build when a
 * backend `log_event` code (literal or composed) lacks a translation, so the raw
 * fallback is a safety net rather than the normal path.
 */
export function eventLabel(t: TFunction, code: string): string {
  const exact = t(`logEvent.${code}`, { defaultValue: '' });
  if (exact) return exact;
  if (code.startsWith('telegram_')) {
    const composed = telegramLabel(t, code);
    if (composed) return composed;
  }
  return code;
}
