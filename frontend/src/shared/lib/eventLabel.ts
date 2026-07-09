import type { TFunction } from 'i18next';

/**
 * Localize a backend log event code. The API is locale-neutral — it emits stable
 * snake_case codes and the SPA owns the labels. The `logEvent.<code>` entries in
 * `ru.json`/`en.json` are the single source of truth: a known code resolves to its
 * translation, and an unmapped code falls back to its raw code (never a blank cell
 * or a `logEvent.foo` key placeholder).
 *
 * A CI parity test (`tests/test_logevent_i18n_parity.py`) fails the build when a
 * backend `log_event` code lacks a translation, so the raw fallback is a safety
 * net rather than the normal path.
 */
export function eventLabel(t: TFunction, code: string): string {
  return t(`logEvent.${code}`, { defaultValue: code });
}
