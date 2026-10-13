import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import { warmingSurvivalQueryOptions } from '@/entities/warming';
import { FOCUS_RING } from '@/shared/design-system';
import { cn } from '@/shared/lib';
import { StatTile } from '@/shared/ui';

import { counterOf, headlineWindow, shareOf, WINDOW_DAYS } from '../lib/share';

// Survival moves with @SpamBot answers — hours apart, not seconds — so a slow poll is
// enough; the panel shares this query when it shows the start-week cut.
export const SURVIVAL_POLL_MS = 5 * 60_000;

// The header's fourth tile: «Живы через 7 дн: 7 из 9 · 2 без проверки». A click opens
// the survival panel the page renders under its banners.
export function SurvivalTile({
  expanded,
  onToggle,
  controls,
  className,
}: {
  className?: string;
  expanded: boolean;
  onToggle: () => void;
  // The id of the panel the tile opens, for `aria-controls`.
  controls: string;
}) {
  const { t } = useTranslation();
  const { data } = useQuery({
    ...warmingSurvivalQueryOptions({ query: { cohort: 'week' } }),
    refetchInterval: SURVIVAL_POLL_MS,
  });
  const window = headlineWindow(data);
  const counter = counterOf(data, window);
  const share = shareOf(counter);
  const unchecked = counter?.unchecked ?? 0;
  const label = [
    t('warming.survival.tile.label', { days: WINDOW_DAYS[window] }),
    unchecked > 0 ? t('warming.survival.tile.unchecked', { count: unchecked }) : null,
    share.percent === null ? null : t('warming.survival.percent', { value: share.percent }),
  ]
    .filter(Boolean)
    .join(' · ');
  const value =
    share.decided > 0
      ? t('warming.survival.ofTotal', { alive: share.alive, total: share.decided })
      : t('warming.survival.tile.none');
  return (
    <button
      type="button"
      aria-expanded={expanded}
      aria-controls={controls}
      title={expanded ? t('warming.survival.tile.hide') : t('warming.survival.tile.show')}
      onClick={onToggle}
      className={cn(
        'rounded-md border border-info-hairline bg-surface-card px-4 py-4 text-left transition-colors duration-state hover:bg-surface',
        expanded && 'border-action-primary',
        FOCUS_RING,
        className,
      )}
    >
      <StatTile label={label} value={value} tone={share.decided > 0 ? 'success' : 'default'} />
    </button>
  );
}
