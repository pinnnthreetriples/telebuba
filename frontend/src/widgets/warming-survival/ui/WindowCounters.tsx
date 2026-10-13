import { useTranslation } from 'react-i18next';

import type { SurvivalReport } from '@/shared/api';
import { cn } from '@/shared/lib';

import { counterOf, headlineWindow, shareOf, type SurvivalWindow } from '../lib/share';

const WINDOWS: readonly SurvivalWindow[] = ['warming', 'd7', 'd30'];

// The three counters: during warming, after 7 days, after 30 days. Each says «3 из 4»
// with a grey percentage only once its denominator can carry one; the headline window
// (7 days until the 30-day one has enough answers) stands large.
export function WindowCounters({ report }: { report: SurvivalReport }) {
  const { t } = useTranslation();
  const headline = headlineWindow(report);
  return (
    <div className="grid grid-cols-1 overflow-hidden rounded-md border border-info-hairline sm:grid-cols-3">
      {WINDOWS.map((window) => {
        const counter = counterOf(report, window);
        const share = shareOf(counter);
        const details = [
          counter?.unchecked
            ? t('warming.survival.detail.unchecked', { count: counter.unchecked })
            : null,
          counter?.wounded
            ? t('warming.survival.detail.wounded', { count: counter.wounded })
            : null,
          counter?.pending
            ? t('warming.survival.detail.pending', { count: counter.pending })
            : null,
          counter?.censored
            ? t('warming.survival.detail.censored', { count: counter.censored })
            : null,
        ].filter(Boolean);
        return (
          <div
            key={window}
            data-testid={`survival-window-${window}`}
            className="border-info-hairline bg-surface-card px-4 py-4 max-sm:[&+&]:border-t sm:[&+&]:border-l"
          >
            <div className="type-small-medium">{t(`warming.survival.window.${window}`)}</div>
            <div className="mt-1 flex items-baseline gap-2">
              <span className={cn(window === headline ? 'type-h1' : 'type-h3', 'tabular-nums')}>
                {t('warming.survival.ofTotal', { alive: share.alive, total: share.decided })}
              </span>
              {share.percent === null ? null : (
                <span className="type-small">
                  {t('warming.survival.percent', { value: share.percent })}
                </span>
              )}
            </div>
            {details.length > 0 ? (
              <div className="mt-1 type-small">{details.join(' · ')}</div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
