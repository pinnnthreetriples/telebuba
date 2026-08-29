import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, Icon, Spinner } from '@/shared/ui';

import { invalidateAccountViews, spamCheckAccountMutation } from '@/entities/account';
import type { AccountRead } from '@/shared/api';
import { useClearedTimeouts } from '@/shared/lib';

import { Section } from './_shared';
import { type CheckState } from './_styles';

// Real spam-status dot per verdict (matches the design's traffic-light tints).
const SPAM_DOT: Record<NonNullable<AccountRead['spam_status']>, string> = {
  clean: 'bg-success',
  limited: 'bg-danger',
  unknown: 'bg-line-strong',
};

// Spam/ban signals card: read-only signals list, refreshed by the @SpamBot check
// in the section header.
export function SignalsSection({ account }: { account: AccountRead }) {
  const { t } = useTranslation();
  const [spamCheck, setSpamCheck] = useState<CheckState>('idle');
  const tipId = useId();
  const queryClient = useQueryClient();
  const later = useClearedTimeouts();
  const spamMutation = useMutation(spamCheckAccountMutation());

  // Real @SpamBot probe; the result also refreshes the signals on next load.
  const runSpamCheck = () => {
    setSpamCheck('loading');
    spamMutation.mutate(
      { path: { account_id: account.account_id } },
      {
        onSuccess: (verdict) => {
          setSpamCheck(verdict.status === 'clean' ? 'ok' : 'err');
          later(() => {
            setSpamCheck('idle');
          }, 2400);
          invalidateAccountViews(queryClient);
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
      dot: account.last_checked_at ? 'bg-success' : 'bg-line-strong',
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
          <Button
            size="xs"
            aria-describedby={tipId}
            onClick={runSpamCheck}
            className={`gap-sm rounded-full ${
              spamCheck === 'ok'
                ? 'border-success bg-success-deep text-on-action hover:border-success'
                : spamCheck === 'err'
                  ? 'border-danger bg-danger text-on-action hover:border-danger'
                  : 'text-content-muted'
            }`}
          >
            {spamCheck === 'loading' && <Spinner />}
            {spamCheck === 'ok' && (
              <span className="tb-blur inline-flex">
                <Icon name="check" size={14} className="stroke-white" />
              </span>
            )}
            {spamCheck === 'err' && (
              <span className="tb-blur inline-flex">
                <Icon name="close" size={14} className="stroke-white" />
              </span>
            )}
            {t('accounts.edit.signalsCheck')}
          </Button>
          <span id={tipId} role="tooltip" className="tb-tip-pop">
            {t('accounts.edit.signalsTip')}
          </span>
        </span>
      }
    >
      <div className="mb-sm type-prose">{t('accounts.edit.signalsReadonly')}</div>
      <div className="flex flex-col">
        {signals.map((signal) => (
          <div
            key={signal.label}
            className="flex items-center justify-between gap-md border-b border-line-row py-md"
          >
            <span className="flex items-center gap-sm type-prose">
              <span className={`size-dot shrink-0 rounded-full ${signal.dot}`} />
              {signal.label}
            </span>
            <span className="text-right type-label text-content-primary">{signal.value}</span>
          </div>
        ))}
      </div>
    </Section>
  );
}
