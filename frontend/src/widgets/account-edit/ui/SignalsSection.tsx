import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { spamCheckAccountMutation } from '@/entities/account';
import type { AccountRead } from '@/shared/api';

import { Section, Spinner } from './_shared';
import { type CheckState } from './_styles';

// Real spam-status dot per verdict (matches the design's traffic-light tints).
const SPAM_DOT: Record<NonNullable<AccountRead['spam_status']>, string> = {
  clean: 'bg-[#2e9e64]',
  limited: 'bg-[#c0473f]',
  unknown: 'bg-line-strong',
};

// Spam/ban signals card: read-only signals list, refreshed by the @SpamBot check
// in the section header.
export function SignalsSection({ account }: { account: AccountRead }) {
  const { t } = useTranslation();
  const [spamCheck, setSpamCheck] = useState<CheckState>('idle');
  const queryClient = useQueryClient();
  const spamMutation = useMutation(spamCheckAccountMutation());

  // Real @SpamBot probe; the result also refreshes the signals on next load.
  const runSpamCheck = () => {
    setSpamCheck('loading');
    spamMutation.mutate(
      { path: { account_id: account.account_id } },
      {
        onSuccess: (verdict) => {
          setSpamCheck(verdict.status === 'clean' ? 'ok' : 'err');
          window.setTimeout(() => {
            setSpamCheck('idle');
          }, 2400);
          void queryClient.invalidateQueries();
        },
        onError: () => {
          setSpamCheck('err');
        },
      },
    );
  };

  const spamStatus = account.spam_status;
  const signals = [
    {
      dot: spamStatus ? SPAM_DOT[spamStatus] : 'bg-line-strong',
      label: t('accounts.edit.signalStatus'),
      value: t(`accounts.edit.spam.${spamStatus ?? 'unknown'}`),
    },
    {
      dot: spamStatus === 'limited' ? SPAM_DOT.limited : 'bg-line-strong',
      label: t('accounts.edit.signalBlock'),
      value:
        spamStatus === 'limited'
          ? (account.spam_detail ?? t('accounts.edit.signalRecorded'))
          : t('accounts.edit.signalNone'),
    },
    {
      dot: account.last_checked_at ? 'bg-[#2e9e64]' : 'bg-line-strong',
      label: t('accounts.edit.signalChecked'),
      value: account.last_checked_at
        ? account.last_checked_at.slice(0, 10)
        : t('accounts.edit.signalNever'),
    },
  ];

  return (
    <Section
      title={t('accounts.edit.signals')}
      right={
        <span className="tb-tip">
          <button
            type="button"
            onClick={runSpamCheck}
            className={`inline-flex items-center gap-[6px] rounded-full px-3 py-[5px] text-[12px] font-medium transition-[background-color,border-color,color] duration-300 ${
              spamCheck === 'ok'
                ? 'border border-[#2e9e64] bg-[#2e9e64] text-white'
                : spamCheck === 'err'
                  ? 'border border-[#c0473f] bg-[#c0473f] text-white'
                  : 'border border-line-input bg-white text-ink-muted'
            }`}
          >
            {spamCheck === 'loading' && <Spinner size={13} />}
            {spamCheck === 'ok' && (
              <span className="tb-blur inline-flex">
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="#fff"
                  strokeWidth="2.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M20 6 9 17l-5-5" />
                </svg>
              </span>
            )}
            {spamCheck === 'err' && (
              <span className="tb-blur inline-flex">
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="#fff"
                  strokeWidth="2.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M18 6 6 18" />
                  <path d="m6 6 12 12" />
                </svg>
              </span>
            )}
            {t('accounts.edit.signalsCheck')}
          </button>
          <span className="tb-tip-pop">{t('accounts.edit.signalsTip')}</span>
        </span>
      }
    >
      <div className="mb-2 text-[12px] text-ink-subtle">{t('accounts.edit.signalsReadonly')}</div>
      <div className="flex flex-col">
        {signals.map((signal) => (
          <div
            key={signal.label}
            className="flex items-center justify-between gap-3 border-b border-[#f0eeeb] py-[11px]"
          >
            <span className="flex items-center gap-2 text-[12.5px] text-ink-muted">
              <span className={`h-[7px] w-[7px] shrink-0 rounded-full ${signal.dot}`} />
              {signal.label}
            </span>
            <span className="text-right text-[12.5px] font-medium text-ink">{signal.value}</span>
          </div>
        ))}
      </div>
    </Section>
  );
}
