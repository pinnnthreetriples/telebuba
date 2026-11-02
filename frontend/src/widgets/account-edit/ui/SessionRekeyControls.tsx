import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountRekeyQueryKey,
  accountRekeyQueryOptions,
  accountSessionAuditQueryKey,
  invalidateAccountViews,
  rekeyAccountSessionMutation,
} from '@/entities/account';
import { formatLocalDateTime } from '@/shared/lib';
import { Button, ConfirmModal } from '@/shared/ui';

// «Re-key the session»: the strongest protection, and never automatic. The imported session
// shares its key with whoever kept a copy; this signs the account in again as a session of
// its own and ends the imported one. Ending it may have to wait for Telegram (a session
// younger than a day cannot end others), so the card says when the old session closes.
export function SessionRekeyControls({
  accountId,
  open,
  hasProxy,
}: {
  accountId: string;
  // Read the state only while the card is open.
  open: boolean;
  hasProxy: boolean;
}) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const path = { path: { account_id: accountId } };
  const state = useQuery({ ...accountRekeyQueryOptions(path), enabled: open });
  const rekey = useMutation(rekeyAccountSessionMutation());
  const when = (iso: string) => formatLocalDateTime(Date.parse(iso), i18n.language);
  const view = state.data;

  const run = () =>
    rekey
      .mutateAsync(path)
      .then((answer) => {
        queryClient.setQueryData(accountRekeyQueryKey(path), answer);
      })
      .finally(() => {
        invalidateAccountViews(queryClient);
        // The sessions listed now include a new key and not the old one.
        void queryClient.invalidateQueries({ queryKey: accountSessionAuditQueryKey(path) });
      });

  return (
    <div className="mt-4 border-t border-line pt-4">
      <p className="mb-3 type-body text-content-subtle">{t('accounts.edit.rekeyHint')}</p>
      <div className="flex flex-wrap items-center gap-3">
        <Button
          disabled={!hasProxy || rekey.isPending}
          onClick={() => {
            setConfirming(true);
          }}
        >
          {t('accounts.edit.rekey')}
        </Button>
        {view ? (
          <span className="type-small text-content-subtle">
            {view.rekeyed_at === null || view.rekeyed_at === undefined
              ? t('accounts.edit.rekeyNever')
              : view.closed_at
                ? t('accounts.edit.rekeyClosed', { date: when(view.closed_at) })
                : view.close_after
                  ? t('accounts.edit.rekeyScheduled', { date: when(view.close_after) })
                  : t('accounts.edit.rekeyDone', { date: when(view.rekeyed_at) })}
          </span>
        ) : null}
      </div>
      {confirming ? (
        <ConfirmModal
          title={t('accounts.edit.rekeyTitle')}
          body={t('accounts.edit.rekeyBody')}
          confirmLabel={t('accounts.edit.rekey')}
          cancelLabel={t('accounts.edit.cancel')}
          onClose={() => {
            setConfirming(false);
          }}
          onConfirm={run}
        />
      ) : null}
    </div>
  );
}
