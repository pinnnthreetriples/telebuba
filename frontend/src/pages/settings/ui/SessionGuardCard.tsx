import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import { sessionGuardStatusQueryOptions } from '@/entities/account';
import { formatLocalDateTime } from '@/shared/lib';
import { Card, Notice } from '@/shared/ui';

// How often the card asks again: the guard itself makes a pass every five minutes.
const REFRESH_MS = 60_000;

// The session guard's heartbeat. It reads the fleet's sessions and catches a dead key; it
// once went silent for 41 hours unnoticed. The backend's watchdog already tells the bot
// when it stalls; this card lets the operator see it at a glance too.
export function SessionGuardCard() {
  const { t, i18n } = useTranslation();
  const status = useQuery({ ...sessionGuardStatusQueryOptions(), refetchInterval: REFRESH_MS });
  const view = status.data;
  const last = view?.last_activity_at
    ? formatLocalDateTime(Date.parse(view.last_activity_at), i18n.language)
    : null;

  return (
    <Card title={t('settings.sessionGuard.title')} subtitle={t('settings.sessionGuard.hint')}>
      {!view ? null : view.stalled ? (
        <Notice tone="danger" role="alert">
          {view.reason === 'stopped'
            ? t('settings.sessionGuard.stopped')
            : t('settings.sessionGuard.silent', { date: last ?? '—' })}
        </Notice>
      ) : (
        <p className="type-body text-content-subtle" role="status">
          {view.running
            ? t('settings.sessionGuard.running', { date: last ?? '—' })
            : t('settings.sessionGuard.notRunning')}
        </p>
      )}
    </Card>
  );
}
