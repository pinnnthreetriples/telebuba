// Trust 3-tier tone (design): healthy / watch / risk. Tokens, not hexes, so the
// tiers read the same as every other health signal in the dashboard.
export function trustTone(trust: number): string {
  if (trust >= 70) return 'text-success-deep';
  if (trust >= 45) return 'text-warning-deep';
  return 'text-danger';
}

// Map a backend readiness reason (English, from evaluate_readiness) to its i18n key:
// "session <status>" / "no proxy" / "proxy failed" / "no channels" / "spam limited" /
// "trust critical". An unknown reason maps to '' and the caller shows it raw.
const READINESS_REASON_KEY: Record<string, string> = {
  'no proxy': 'warming.notReady.noProxy',
  'proxy failed': 'warming.notReady.proxyFailed',
  'no channels': 'warming.notReady.noChannels',
  'spam limited': 'warming.notReady.spamLimited',
  'trust critical': 'warming.notReady.trustCritical',
};

export function readinessReasonKey(reason: string): string {
  return reason.startsWith('session ')
    ? 'warming.notReady.session'
    : (READINESS_REASON_KEY[reason] ?? '');
}
