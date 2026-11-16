import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountRekeyQueryKey,
  accountRekeyQueryOptions,
  accountSessionAuditQueryKey,
  closeAccountOldKeyMutation,
  invalidateAccountViews,
  rekeyAccountSessionMutation,
} from '@/entities/account';
import type { AccountRekeyView, OldKeyCheck } from '@/shared/api';
import { formatLocalDateTime, mutationErrorText } from '@/shared/lib';
import { Button, ConfirmModal, Notice } from '@/shared/ui';

// «Re-key the session»: the strongest protection, and never automatic. The imported session
// shares its key with whoever kept a copy; this signs the account in again as a session of
// its own. Ending the imported (old) session is a separate step: by default the operator
// does it from here, after the checklist (`view.checklist`), because an account once lost
// its new key a second after Telebuba ended an old official session on its own. With the
// setting on (`view.auto_close`) Telebuba ends it itself and the card says when.
// Every outcome stays on the card after the dialog closes: a failure says why, a success
// says whether the old session is closed. A row Telebuba gave up on (`problem`) says so
// instead, and the operator settles it in the session list.
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
  const [closing, setClosing] = useState(false);
  const path = { path: { account_id: accountId } };
  const state = useQuery({ ...accountRekeyQueryOptions(path), enabled: open });
  const rekey = useMutation({ ...rekeyAccountSessionMutation(), meta: { inlineError: true } });
  const closeOld = useMutation({ ...closeAccountOldKeyMutation(), meta: { inlineError: true } });
  const [failure, setFailure] = useState<string | null>(null);
  const [closeFailure, setCloseFailure] = useState<string | null>(null);
  const [completed, setCompleted] = useState<AccountRekeyView | null>(null);
  const when = (iso: string) => formatLocalDateTime(Date.parse(iso), i18n.language);
  const view = state.data;
  const oldOpen = Boolean(view?.rekeyed_at && !view.closed_at && !view.problem);
  const checklist: OldKeyCheck[] = view?.checklist ?? [];
  const allMet = checklist.every((check) => check.ok);

  const settle = () => {
    invalidateAccountViews(queryClient);
    // The sessions listed now include a new key, or no longer the old one.
    void queryClient.invalidateQueries({ queryKey: accountSessionAuditQueryKey(path) });
  };

  const run = () => {
    setFailure(null);
    setCloseFailure(null);
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
        .finally(settle)
    );
  };

  const closeOldKey = () => {
    setCloseFailure(null);
    setCompleted(null);
    return closeOld
      .mutateAsync(path)
      .then((answer) => {
        queryClient.setQueryData(accountRekeyQueryKey(path), answer);
      })
      .catch((error: unknown) => {
        setCloseFailure(mutationErrorText(error));
      })
      .finally(settle);
  };

  const statusLine = (current: AccountRekeyView) => {
    if (!current.rekeyed_at) return t('accounts.edit.rekeyNever');
    if (current.closed_at) return t('accounts.edit.rekeyClosed', { date: when(current.closed_at) });
    if (current.problem) return null;
    if (current.close_after) {
      return current.auto_close
        ? t('accounts.edit.rekeyScheduled', { date: when(current.close_after) })
        : t('accounts.edit.rekeyOldRetryAfter', { date: when(current.close_after) });
    }
    return current.auto_close
      ? t('accounts.edit.rekeyDone', { date: when(current.rekeyed_at) })
      : t('accounts.edit.rekeyOldOpen');
  };

  const checklistItems = (
    <ul className="flex flex-col gap-1" aria-label={t('accounts.edit.oldKeyChecklist')}>
      {checklist.map((check) => (
        <li key={check.name} className={check.ok ? 'text-content' : 'text-danger'}>
          {check.ok ? '✓ ' : '✗ '}
          {t(`accounts.edit.oldKeyCheck.${check.name}`)}
        </li>
      ))}
    </ul>
  );

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
        {view ? <span className="type-small text-content-subtle">{statusLine(view)}</span> : null}
      </div>
      {oldOpen && view && !view.auto_close ? (
        <div className="mt-3 flex flex-col gap-2">
          <p className="type-small text-content-subtle">{t('accounts.edit.oldKeyHint')}</p>
          {checklistItems}
          <div>
            <Button
              disabled={!hasProxy || closeOld.isPending}
              onClick={() => {
                setClosing(true);
              }}
            >
              {t('accounts.edit.closeOldKey')}
            </Button>
          </div>
        </div>
      ) : null}
      {view?.problem && !view.closed_at ? (
        <div className="mt-3">
          <Notice tone="warning" role="status">
            {view.problem === 'old_key_is_ours'
              ? t('accounts.edit.rekeyProblemIsOurs')
              : view.problem === 'rekey_incomplete'
                ? t('accounts.edit.rekeyProblemIncomplete')
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
      {closeFailure ? (
        <div className="mt-3">
          <Notice tone="danger" role="alert">
            {t('accounts.edit.closeOldKeyFailed', { reason: closeFailure })}
          </Notice>
        </div>
      ) : null}
      {completed && !completed.problem ? (
        <div className="mt-3">
          <Notice tone={completed.closed_at ? 'success' : 'warning'} role="status">
            {completed.closed_at
              ? t('accounts.edit.rekeySucceeded')
              : !completed.auto_close
                ? t('accounts.edit.rekeySucceededManual')
                : completed.close_after
                  ? t('accounts.edit.rekeySucceededScheduled', {
                      date: when(completed.close_after),
                    })
                  : t('accounts.edit.rekeySucceeded')}
          </Notice>
        </div>
      ) : null}
      {confirming ? (
        <ConfirmModal
          title={t('accounts.edit.rekeyTitle')}
          body={
            <>
              <p>
                {view?.auto_close ? t('accounts.edit.rekeyBodyAuto') : t('accounts.edit.rekeyBody')}
              </p>
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
      {closing ? (
        <ConfirmModal
          title={t('accounts.edit.closeOldKeyTitle')}
          body={
            <div className="flex flex-col gap-3">
              <p>{t('accounts.edit.closeOldKeyBody')}</p>
              {checklistItems}
              {allMet ? null : (
                <p className="text-danger" role="status">
                  {t('accounts.edit.closeOldKeyWarning')}
                </p>
              )}
            </div>
          }
          confirmLabel={t('accounts.edit.closeOldKey')}
          cancelLabel={t('accounts.edit.cancel')}
          onClose={() => {
            setClosing(false);
          }}
          onConfirm={closeOldKey}
        />
      ) : null}
    </div>
  );
}
