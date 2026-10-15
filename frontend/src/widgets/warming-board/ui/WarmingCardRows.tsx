import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';

import type { WarmingAccountState } from '@/shared/api';
import { formatLocalTime } from '@/shared/lib';
import { Icon, Notice } from '@/shared/ui';

import { readinessReasonKey } from '../lib/trust';

// The operator-console rows of a warming card (Прогрев 2.0, item 2.3). The backend
// sends codes — phase, DM status, the one attention item — and these rows only word
// them; none of them re-derives a decision the engine already made.

function attentionText(t: TFunction, account: WarmingAccountState): string | null {
  const item = account.attention;
  if (!item) return null;
  const time = item.until ? formatLocalTime(item.until) : null;
  if (item.code === 'quarantine') {
    const label = t('warming.console.attention.quarantine', {
      attempt: item.attempt ?? 1,
      attempts: item.attempts ?? 1,
    });
    return time ? `${label} · ${t('warming.console.until', { time })}` : label;
  }
  if (item.code === 'flood_wait') {
    return t('warming.console.attention.flood_wait', { time: time ?? '—' });
  }
  if (item.code === 'not_ready') {
    const reasons = (item.reasons ?? [])
      .map((reason) => {
        const key = readinessReasonKey(reason);
        return key ? t(key) : reason;
      })
      .join(', ');
    return t('warming.console.attention.not_ready', { reasons });
  }
  return t(`warming.console.attention.${item.code}`);
}

// Row 5, only when something is wrong: «Карантин 2/3», «Флуд до 14:30», «Не готов: …».
export function AttentionRow({ account }: { account: WarmingAccountState }) {
  const { t } = useTranslation();
  const text = attentionText(t, account);
  if (!text) return null;
  return (
    <div>
      <Notice tone="warning" role="status" className="flex items-center gap-2 py-2 type-small">
        <Icon name="alert-triangle" size={14} className="shrink-0" />
        {text}
      </Notice>
    </div>
  );
}
