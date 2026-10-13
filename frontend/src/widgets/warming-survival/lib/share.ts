import type { SurvivalCounter, SurvivalReport } from '@/shared/api';

// With 10–15 accounts a percentage lies — one death moves it by seven points — so the
// panel says «3 из 4», and a grey percentage joins it only from this many decided
// accounts on.
export const PERCENT_FROM = 10;
// A smoothed survival curve only means something past ~30 releases. It is not drawn
// yet; the threshold is kept so the panel can say why.
export const CURVE_FROM_RELEASES = 30;

export type SurvivalWindow = SurvivalCounter['window'];

export interface Share {
  alive: number;
  // Alive plus dead: the ones the window has an answer for. Unchecked are not in it.
  decided: number;
  // A whole percentage, or `null` while the denominator is too small to carry one.
  percent: number | null;
}

export function shareOf(counter: SurvivalCounter | undefined): Share {
  const alive = counter?.alive ?? 0;
  const decided = alive + (counter?.dead ?? 0);
  const percent = decided >= PERCENT_FROM ? Math.round((alive / decided) * 100) : null;
  return { alive, decided, percent };
}

export function counterOf(
  report: Pick<SurvivalReport, 'windows'> | undefined,
  window: SurvivalWindow,
): SurvivalCounter | undefined {
  return report?.windows?.find((counter) => counter.window === window);
}

// Q9: both windows are shown, the 7-day one large until the 30-day one has as many
// decided accounts as a percentage needs.
export function headlineWindow(report: Pick<SurvivalReport, 'windows'> | undefined): 'd7' | 'd30' {
  return shareOf(counterOf(report, 'd30')).decided >= PERCENT_FROM ? 'd30' : 'd7';
}

export const WINDOW_DAYS: Record<'d7' | 'd30', number> = { d7: 7, d30: 30 };

// `2026-W37` → `37`; anything else is returned as it came.
export function isoWeekNumber(key: string): string {
  const match = /^\d{4}-W(\d{2})$/.exec(key);
  return match ? String(Number(match[1])) : key;
}
