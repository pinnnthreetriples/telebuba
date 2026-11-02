import type { TFunction } from 'i18next';

import type { SessionCloseOutcome } from '@/shared/api';

// What became of one session a close run named, in words. `too_new` carries the time
// left when Telegram's rule (a session younger than a day cannot end others) is known.
export function sessionOutcomeText(t: TFunction, outcome: SessionCloseOutcome): string {
  const seconds = outcome.retry_after_seconds;
  if (outcome.status === 'too_new' && seconds) {
    const hours = Math.max(1, Math.ceil(seconds / 3600));
    return t('accounts.edit.sessionsOutcome.too_new_wait', { hours });
  }
  if (outcome.status === 'flood_wait') {
    return t('accounts.edit.sessionsOutcome.flood_wait', { s: seconds ?? 0 });
  }
  return t(`accounts.edit.sessionsOutcome.${outcome.status}`);
}

// `gone` is done as far as the operator is concerned; anything else that is not
// `closed` is a session still standing.
export function isRefused(outcome: SessionCloseOutcome): boolean {
  return outcome.status !== 'closed' && outcome.status !== 'gone';
}
