import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import { sessionGuardStatusQueryOptions } from '@/entities/account';
import { Notice } from '@/shared/ui';

// Same cadence as the Settings card that reads the same query, so one cache entry serves both.
const REFRESH_MS = 60_000;

// The fleet-level warning on the Accounts page when the session-key copies are off: without
// them a lost disk or a lost file means the account cannot come back. It says nothing while
// loading, on a failed read and while the copies are on; there is no dismiss, the only way
// out is turning them on in Settings.
export function SessionCopiesWarning() {
  const { t } = useTranslation();
  const guard = useQuery({ ...sessionGuardStatusQueryOptions(), refetchInterval: REFRESH_MS });
  if (guard.data?.backups?.session_vault_enabled !== false) return null;

  return (
    <div className="mb-4">
      <Notice tone="warning" role="status" data-testid="session-copies-warning">
        <p className="m-0 type-body-medium">{t('accounts.sessionCopiesOff.text')}</p>
        <a className="type-small text-action-primary underline" href="/settings">
          {t('accounts.sessionCopiesOff.link')}
        </a>
      </Notice>
    </div>
  );
}
