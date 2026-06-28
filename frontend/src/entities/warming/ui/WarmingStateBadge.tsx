import { useTranslation } from 'react-i18next';

import type { WarmingAccountState } from '@/shared/api';
import { cn } from '@/shared/lib';

type WarmingState = WarmingAccountState['state'];

const STATE_CLASS: Record<WarmingState, string> = {
  idle: 'bg-line text-ink-muted',
  active: 'bg-primary-tint text-primary',
  sleeping: 'bg-line text-ink-muted',
  flood_wait: 'bg-warning/10 text-warning',
  quarantine: 'bg-warning/10 text-warning',
  error: 'bg-danger-tint text-danger',
};

export function WarmingStateBadge({ state }: { state: WarmingState }) {
  const { t } = useTranslation();
  return (
    <span
      className={cn(
        'inline-flex items-center rounded px-2 py-0.5 text-xs font-medium',
        STATE_CLASS[state],
      )}
    >
      {t(`warming.state.${state}`)}
    </span>
  );
}
