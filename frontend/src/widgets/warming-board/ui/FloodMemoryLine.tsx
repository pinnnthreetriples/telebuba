import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import type { WarmingAccountState } from '@/shared/api';

type FloodMemory = Pick<WarmingAccountState, 'flood_total' | 'dm_blocked_until' | 'limit_factor'>;

// Flood memory on the warming card: lifetime spam blocks, the day DMs reopen after
// the last quarantine release, and today's share of the daily limit while it ramps
// back. The backend clears `dm_blocked_until` once DMs reopen and returns a factor of
// 1 once the ramp ends, so a part shows only while it says something.
export function FloodMemoryLine({ account }: { account: FloodMemory }) {
  const { t, i18n } = useTranslation();
  const formats = useMemo(
    () => ({
      day: new Intl.DateTimeFormat(i18n.language, { day: 'numeric', month: 'short' }),
      percent: new Intl.NumberFormat(i18n.language, {
        style: 'percent',
        maximumFractionDigits: 0,
      }),
    }),
    [i18n.language],
  );
  const parts: string[] = [];
  const total = account.flood_total ?? 0;
  if (total > 0) parts.push(t('warming.card.floodTotal', { total }));
  const dmUntil = account.dm_blocked_until ? new Date(account.dm_blocked_until) : null;
  if (dmUntil && !Number.isNaN(dmUntil.getTime())) {
    parts.push(t('warming.card.dmBlockedUntil', { date: formats.day.format(dmUntil) }));
  }
  const factor = account.limit_factor ?? 1;
  if (factor < 1) {
    parts.push(t('warming.card.limitShare', { percent: formats.percent.format(factor) }));
  }
  if (parts.length === 0) return null;
  return <p className="mb-3 type-small text-content-muted">{parts.join(' · ')}</p>;
}
