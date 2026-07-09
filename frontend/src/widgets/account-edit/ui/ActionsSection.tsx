import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  checkAccountMutation,
  deleteAccountMutation,
  resetAccountSessionMutation,
} from '@/entities/account';
import type { AccountRead } from '@/shared/api';
import { FeedbackMark, Modal } from '@/shared/ui';

import { Section, Spinner } from './_shared';
import { type CheckState } from './_styles';

// Actions card: liveness check, reset-session, and delete-account (with a
// confirm modal). `onBack` returns to the list after a successful delete.
export function ActionsSection({ account, onBack }: { account: AccountRead; onBack: () => void }) {
  const { t } = useTranslation();
  const [aliveCheck, setAliveCheck] = useState<CheckState>('idle');
  const [resetCheck, setResetCheck] = useState<CheckState>('idle');
  const [confirmDelete, setConfirmDelete] = useState(false);

  const queryClient = useQueryClient();
  const aliveMutation = useMutation(checkAccountMutation());
  const resetSession = useMutation(resetAccountSessionMutation());
  const deleteAccount = useMutation(deleteAccountMutation());
  const invalidate = () => {
    void queryClient.invalidateQueries();
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
          window.setTimeout(() => {
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
        window.setTimeout(() => {
          setResetCheck('idle');
        }, 1600);
      },
    });
  };

  const onDelete = () => {
    deleteAccount.mutate(path, {
      onSuccess: () => {
        invalidate();
        onBack();
      },
    });
  };

  return (
    <>
      <Section title={t('accounts.edit.actions')} bodyClassName="px-5 pb-[6px]">
        <div className="flex items-center justify-between gap-3 border-b border-[#f0eeeb] py-[14px]">
          <div>
            <div className="text-[13px] font-medium">{t('accounts.edit.aliveTitle')}</div>
            <div
              className="mt-px text-[11.5px]"
              style={{
                color:
                  aliveCheck === 'ok' ? '#2e9e64' : aliveCheck === 'err' ? '#c0473f' : '#9a9893',
              }}
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
            title={t('accounts.edit.aliveBtnTitle')}
            aria-label={t('accounts.edit.aliveBtnTitle')}
            className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-full border transition-[background-color,border-color,color] duration-300"
            style={{
              borderColor:
                aliveCheck === 'ok' ? '#2e9e64' : aliveCheck === 'err' ? '#c0473f' : '#e6e5e3',
              background:
                aliveCheck === 'ok' ? '#2e9e64' : aliveCheck === 'err' ? '#c0473f' : '#fff',
              color: aliveCheck === 'ok' || aliveCheck === 'err' ? '#fff' : '#74726e',
            }}
          >
            {aliveCheck === 'idle' && (
              <span className="tb-blur inline-flex">
                <svg
                  width="17"
                  height="17"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M21 12a9 9 0 1 1-6.2-8.6" />
                  <path d="M21 3v6h-6" />
                </svg>
              </span>
            )}
            {aliveCheck === 'loading' && <Spinner size={15} />}
            {aliveCheck === 'ok' && (
              <span className="tb-blur inline-flex">
                <svg
                  width="18"
                  height="18"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M20 6 9 17l-5-5" />
                </svg>
              </span>
            )}
            {aliveCheck === 'err' && (
              <span className="tb-blur inline-flex">
                <svg
                  width="18"
                  height="18"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M18 6 6 18" />
                  <path d="m6 6 12 12" />
                </svg>
              </span>
            )}
          </button>
        </div>
        <div className="flex items-center justify-between gap-3 border-b border-[#f0eeeb] py-[14px]">
          <div>
            <div className="text-[13px] font-medium">{t('accounts.edit.resetSession')}</div>
            <div className="mt-px text-[11.5px] text-ink-subtle">
              {t('accounts.edit.resetSessionHint')}
            </div>
          </div>
          <span className="flex shrink-0 items-center gap-[7px]">
            <FeedbackMark
              result={resetCheck === 'idle' || resetCheck === 'loading' ? undefined : resetCheck}
            />
            <button
              type="button"
              onClick={onReset}
              disabled={resetSession.isPending}
              className="rounded-full border border-line-input bg-white px-4 py-2 text-[13px] font-medium disabled:opacity-50"
            >
              {resetCheck === 'loading' ? <Spinner size={14} /> : t('accounts.edit.reset')}
            </button>
          </span>
        </div>
        <div className="flex items-center justify-between gap-3 py-[14px]">
          <div>
            <div className="text-[13px] font-medium">{t('accounts.edit.deleteAccount')}</div>
            <div className="mt-px text-[11.5px] text-ink-subtle">
              {t('accounts.edit.deleteHint')}
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              setConfirmDelete(true);
            }}
            className="shrink-0 px-1 py-2 text-[13px] font-medium text-[#c0473f]"
          >
            {t('accounts.edit.deleteAccount')}
          </button>
        </div>
      </Section>

      {confirmDelete ? (
        <Modal
          onClose={() => {
            setConfirmDelete(false);
          }}
          z={70}
          className="w-[420px]"
        >
          <div className="p-6">
            <div className="mb-2 text-[16px] font-bold">
              {t('accounts.deleteModal.title', { phone: account.phone ?? account.account_id })}
            </div>
            <div className="mb-[22px] text-[13px] leading-[1.5] text-ink-muted">
              {t('accounts.deleteModal.body')}
            </div>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setConfirmDelete(false);
                }}
                className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink"
              >
                {t('accounts.deleteModal.cancel')}
              </button>
              <button
                type="button"
                onClick={() => {
                  setConfirmDelete(false);
                  onDelete();
                }}
                className="rounded-full border border-[#f0c9c5] bg-danger-tint px-5 py-[9px] text-[13px] font-semibold text-danger"
              >
                {t('accounts.deleteModal.confirm')}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </>
  );
}
