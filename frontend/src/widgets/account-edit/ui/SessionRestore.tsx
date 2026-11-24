import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import {
  accountSessionCopiesQueryOptions,
  invalidateAccountViews,
  restoreAccountSessionMutation,
} from '@/entities/account';
import type { AccountRead } from '@/shared/api';
import { formatLocalDateTime } from '@/shared/lib';
import { Button, Notice } from '@/shared/ui';

// Offered only when the backend says a restore can work: the account's own session file is
// gone (or holds no key) AND the vault has a copy of it. The copy is put back and the account
// is checked again; a refusal reaches the operator through the mutation toast.
export function SessionRestore({ account }: { account: AccountRead }) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const path = { path: { account_id: account.account_id } } as const;
  const copies = useQuery(accountSessionCopiesQueryOptions(path));
  const restore = useMutation(restoreAccountSessionMutation());
  if (!copies.data?.can_restore) return null;

  const newest = copies.data.newest_at
    ? formatLocalDateTime(Date.parse(copies.data.newest_at), i18n.language)
    : null;
  return (
    <div className="mb-3">
      <Notice tone="warning" role="status" data-testid="session-restore">
        <p className="m-0 type-body-medium">{t('accounts.edit.restore.title')}</p>
        <p className="m-0 type-small">
          {newest ? t('accounts.edit.restore.copyFrom', { date: newest }) : null}
        </p>
        <Button
          size="sm"
          loading={restore.isPending}
          onClick={() => {
            restore.mutate(path, {
              onSuccess: () => {
                invalidateAccountViews(queryClient);
                void copies.refetch();
              },
            });
          }}
        >
          {t('accounts.edit.restore.action')}
        </Button>
      </Notice>
    </div>
  );
}
