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
import type { AccountRekeyView } from '@/shared/api';
import { formatLocalDateTime, mutationErrorText } from '@/shared/lib';
import { Button, ConfirmModal, Notice } from '@/shared/ui';

// «Re-key the session»: the strongest protection, and never automatic. The imported session
// shares its key with whoever kept a copy; this signs the account in again as a session of
// its own and ends the imported one. Ending it may have to wait for Telegram (a session
// younger than a day cannot end others), so the card says when the old session closes.
// Every outcome stays on the card after the dialog closes: a failure says why and that the
// session is as it was, a success says whether the old session is closed or when it will be.
// A row Telebuba gave up on (`problem`) says so instead: the old session is still alive and
// the operator has to close it in the session list.
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
  const rekey = useMutation({ ...rekeyAccountSessionMutation(), meta: { inlineError: true } });
  const [failure, setFailure] = useState<string | null>(null);
  const [completed, setCompleted] = useState<AccountRekeyView | null>(null);
  const when = (iso: string) => formatLocalDateTime(Date.parse(iso), i18n.language);
  const view = state.data;

  const run = () => {
    setFailure(null);
    setCompleted(null);
    return (
      rekey
        .mutateAsync(path)
        .then((answer) => {
          queryClient.setQueryData(accountRekeyQueryKey(path), answer);
          setCompleted(answer);
        })
        // Answered here, so the dialog closes and the card keeps the reason.
        .catch((error: unknown) => {
          setFailure(mutationErrorText(error));
        })
        .finally(() => {
          invalidateAccountViews(queryClient);
          // The sessions listed now include a new key and not the old one.
          void queryClient.invalidateQueries({ queryKey: accountSessionAuditQueryKey(path) });
        })
    );
  };

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
                : view.problem
                  ? null
                  : view.close_after
                    ? t('accounts.edit.rekeyScheduled', { date: when(view.close_after) })
                    : t('accounts.edit.rekeyDone', { date: when(view.rekeyed_at) })}
          </span>
        ) : null}
      </div>
      {view?.problem && !view.closed_at ? (
        <div className="mt-3">
          <Notice tone="warning" role="status">
            {view.problem === 'old_key_is_ours'
              ? t('accounts.edit.rekeyProblemIsOurs')
              : t('accounts.edit.rekeyProblemUnknown')}
          </Notice>
        </div>
      ) : null}
      {hasProxy ? null : (
        <p className="mt-2 type-small text-content-subtle">{t('accounts.edit.rekeyNoProxy')}</p>
      )}
      {failure ? (
        <div className="mt-3">
          <Notice tone="danger" role="alert">
            {t('accounts.edit.rekeyFailed', { reason: failure })}
          </Notice>
        </div>
      ) : null}
      {completed && !completed.problem ? (
        <div className="mt-3">
          <Notice tone={completed.closed_at ? 'success' : 'warning'} role="status">
            {completed.closed_at || !completed.close_after
              ? t('accounts.edit.rekeySucceeded')
              : t('accounts.edit.rekeySucceededScheduled', { date: when(completed.close_after) })}
          </Notice>
        </div>
      ) : null}
      {confirming ? (
        <ConfirmModal
          title={t('accounts.edit.rekeyTitle')}
          body={
            <>
              <p>{t('accounts.edit.rekeyBody')}</p>
              {rekey.isPending ? (
                <p className="mt-2 text-content" role="status">
                  {t('accounts.edit.rekeyRunning')}
                </p>
              ) : null}
            </>
          }
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
