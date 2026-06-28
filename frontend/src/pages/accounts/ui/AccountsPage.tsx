import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import { accountsQueryOptions } from '@/entities/account';

// Tracer screen: proves the end-to-end path (generated client -> TanStack Query
// hook -> rendered component). The full Accounts screen lands in #167.
export function AccountsPage() {
  const { t } = useTranslation();
  const { data, isPending, isError } = useQuery(accountsQueryOptions());

  if (isPending) return <p className="p-8 text-ink-muted">{t('accounts.loading')}</p>;
  if (isError) {
    return (
      <p role="alert" className="p-8 text-danger">
        {t('accounts.error')}
      </p>
    );
  }

  return (
    <main className="mx-auto max-w-3xl p-8">
      <h1 className="mb-4 text-2xl font-semibold">{t('accounts.title')}</h1>
      {data.items.length === 0 ? (
        <p className="text-ink-subtle">{t('accounts.empty')}</p>
      ) : (
        <ul className="divide-y divide-line rounded-md border border-line bg-surface">
          {data.items.map((account) => (
            <li key={account.account_id} className="px-4 py-3 font-mono text-sm">
              {account.account_id}
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
