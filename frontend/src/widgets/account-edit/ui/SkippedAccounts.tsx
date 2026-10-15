import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import { accountDisplayName, allAccountsQueryOptions } from '@/entities/account';
import type { SkippedAccount } from '@/shared/api';

// The accounts a run left out before it started, and why: shown with its results.
export function SkippedAccounts({ accounts }: { accounts: SkippedAccount[] }) {
  const { t } = useTranslation();
  const fleet = useQuery(allAccountsQueryOptions());
  if (accounts.length === 0) return null;
  const byId = new Map((fleet.data?.items ?? []).map((row) => [row.account_id, row]));
  return (
    <div>
      <h3 className="type-body-medium text-content-secondary">
        {t('accounts.messages.skippedAccounts.title', { count: accounts.length })}
      </h3>
      <ul className="mt-2 space-y-2">
        {accounts.map((row) => (
          <li key={row.account_id} className="rounded-sm bg-warning-tint px-3 py-2 type-small">
            {t('accounts.messages.skippedAccounts.item', {
              name: accountDisplayName(byId.get(row.account_id) ?? { account_id: row.account_id }),
              reason: t(`accounts.messages.code.${row.reason}`),
            })}
            {row.retry_after_seconds != null && row.retry_after_seconds > 0 && (
              <span>
                {' '}
                {t('accounts.messages.skippedAccounts.restLeft', {
                  hours: Math.ceil(row.retry_after_seconds / 3600),
                })}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
