import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  checkAccountMutation,
  deleteAccountMutation,
  invalidateAccountViews,
  resetAccountSessionMutation,
} from '@/entities/account';
import type { AccountRead } from '@/shared/api';
import { useClearedTimeouts } from '@/shared/lib';
import { Button, ConfirmModal, FeedbackMark, Icon, Spinner } from '@/shared/ui';

import { Section } from './_shared';
import { type CheckState } from './_styles';

// Actions card: liveness check, reset-session, and delete-account (with a
// confirm modal). `onBack` returns to the list after a successful delete.
export function ActionsSection({ account, onBack }: { account: AccountRead; onBack: () => void }) {
  const { t } = useTranslation();
  const [aliveCheck, setAliveCheck] = useState<CheckState>('idle');
  const [resetCheck, setResetCheck] = useState<CheckState>('idle');
  const [confirmDelete, setConfirmDelete] = useState(false);

  const queryClient = useQueryClient();
  const later = useClearedTimeouts();
  const aliveMutation = useMutation(checkAccountMutation());
  const resetSession = useMutation(resetAccountSessionMutation());
  const deleteAccount = useMutation(deleteAccountMutation());
  const invalidate = () => {
    invalidateAccountViews(queryClient);
  };

  const path = { path: { account_id: account.account_id } } as const;

  // Real liveness check (reuses the accounts-table «Проверить» endpoint).
  const runAliveCheck = () => {
    setAliveCheck('loading');
    aliveMutation.mutate(
      { body: { account_id: account.account_id } },
      {
        onSuccess: (checked) => {
          setAliveCheck(checked.status === 'alive' ? 'ok' : 'err');
          later(() => {
            setAliveCheck('idle');
          }, 2400);
          invalidate();
        },
        onError: () => {
          setAliveCheck('err');
        },
      },
    );
  };

  const onReset = () => {
    setResetCheck('loading');
    resetSession.mutate(path, {
      onSuccess: () => {
        setResetCheck('ok');
        invalidate();
      },
      onError: () => {
        setResetCheck('err');
      },
      onSettled: () => {
        later(() => {
          setResetCheck('idle');
        }, 1600);
      },
    });
  };

  // Returns the promise so ConfirmModal keeps the dialog open (with a pending
  // spinner) until the DELETE actually resolves: it can now legitimately fail
  // — a missing row answers 404 — and the old fire-and-forget closed the dialog
  // first, so a failure left the account listed with no explanation and no
  // `onBack()`.
  const onDelete = () =>
    deleteAccount.mutateAsync(path).then(() => {
      invalidate();
      onBack();
    });

  return (
    <>
      <Section title={t('accounts.edit.actions')} bodyClassName="px-xl pb-tight">
        <div className="flex items-center justify-between gap-md border-b border-line-row py-lg">
          <div>
            <div className="type-card-title">{t('accounts.edit.aliveTitle')}</div>
            {/* Verdict tone from the tokens the states MEAN — alive/dead/unknown. */}
            <div
              className={`mt-px text-tiny ${aliveCheck === 'ok' ? 'text-success-deep' : aliveCheck === 'err' ? 'text-danger' : 'text-content-subtle'}`}
            >
              {aliveCheck === 'ok'
                ? t('accounts.edit.aliveOk')
                : aliveCheck === 'err'
                  ? t('accounts.edit.aliveErr')
                  : t('accounts.edit.aliveHint')}
            </div>
          </div>
          <button
            type="button"
            onClick={runAliveCheck}
            // Same guard the reset button carries: a second click before the
            // first check settles takes over the mutation's one callback slot,
            // so the first result's verdict and invalidate() are dropped — on
            // top of a wasted Telegram round-trip.
            disabled={aliveMutation.isPending}
            title={t('accounts.edit.aliveBtnTitle')}
            aria-label={t('accounts.edit.aliveBtnTitle')}
            className={`flex size-icon shrink-0 items-center justify-center rounded-full border transition-[background-color,border-color,color] duration-enter ${
              aliveCheck === 'ok'
                ? 'border-success bg-success-deep text-on-action'
                : aliveCheck === 'err'
                  ? 'border-danger bg-danger text-on-action'
                  : 'border-line bg-surface-card text-content-muted'
            }`}
          >
            {aliveCheck === 'idle' && (
              <span className="tb-blur inline-flex">
                <Icon name="refresh" size={18} />
              </span>
            )}
            {aliveCheck === 'loading' && <Spinner />}
            {aliveCheck === 'ok' && (
              <span className="tb-blur inline-flex">
                <Icon name="check" size={18} />
              </span>
            )}
            {aliveCheck === 'err' && (
              <span className="tb-blur inline-flex">
                <Icon name="close" size={18} />
              </span>
            )}
          </button>
        </div>
        <div className="flex items-center justify-between gap-md border-b border-line-row py-lg">
          <div>
            <div className="type-card-title">{t('accounts.edit.resetSession')}</div>
            <div className="mt-px type-caption">{t('accounts.edit.resetSessionHint')}</div>
          </div>
          <span className="flex shrink-0 items-center gap-sm">
            <FeedbackMark
              result={resetCheck === 'idle' || resetCheck === 'loading' ? undefined : resetCheck}
            />
            <Button size="sm" onClick={onReset} loading={resetSession.isPending}>
              {t('accounts.edit.reset')}
            </Button>
          </span>
        </div>
        <div className="flex items-center justify-between gap-md py-lg">
          <div>
            <div className="type-card-title">{t('accounts.edit.deleteAccount')}</div>
            <div className="mt-px type-caption">{t('accounts.edit.deleteHint')}</div>
          </div>
          <button
            type="button"
            onClick={() => {
              setConfirmDelete(true);
            }}
            className="shrink-0 px-xs py-sm text-body font-medium text-danger"
          >
            {t('accounts.edit.deleteAccount')}
          </button>
        </div>
      </Section>

      {confirmDelete ? (
        <ConfirmModal
          title={t('accounts.deleteModal.title', { phone: account.phone ?? account.account_id })}
          body={t('accounts.deleteModal.body')}
          confirmLabel={t('accounts.deleteModal.confirm')}
          cancelLabel={t('accounts.deleteModal.cancel')}
          onClose={() => {
            setConfirmDelete(false);
          }}
          onConfirm={onDelete}
        />
      ) : null}
    </>
  );
}
