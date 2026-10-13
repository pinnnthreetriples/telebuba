// Phone/proxy geo for the start dialog. The backend owns the verdict (`geo_status`,
// from `evaluate_geo`) and the consent (`geo_accepted`, remembered per country pair);
// the SPA only asks whether a start still needs one, and names the countries.

interface GeoFacts {
  geo_status?: 'match' | 'mismatch' | 'unknown';
  geo_accepted?: boolean;
}

// A mismatch the operator has not accepted for this very pair of countries.
export function needsGeoConsent(account: GeoFacts): boolean {
  return account.geo_status === 'mismatch' && !(account.geo_accepted ?? false);
}

// "US" → "США" / "United States" in the UI language; the code itself when unknown.
export function regionName(code: string | null | undefined, language: string): string {
  if (!code) return '—';
  try {
    return new Intl.DisplayNames([language], { type: 'region' }).of(code.toUpperCase()) ?? code;
  } catch {
    return code;
  }
}
