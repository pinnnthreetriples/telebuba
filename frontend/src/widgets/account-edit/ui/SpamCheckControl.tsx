import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, ConfirmModal, Icon, toastError } from '@/shared/ui';

import {
  invalidateAccountViews,
  invalidateSpamAppeal,
  spamCheckAccountMutation,
} from '@/entities/account';
import type { AccountRead, SpamCheckResult } from '@/shared/api';
import {
  errorMessage,
  formatRelativeTo,
  mutationErrorText,
  useClearedTimeouts,
} from '@/shared/lib';

import { instantMs, isHeld } from '../model/spamCard';
import { type CheckState } from './_styles';

// What the backend answers when it will not probe: the click was out of turn and unconfirmed.
const NEEDS_CONFIRMATION = 'needs_confirmation';

// The Spam-check button: the age of the last probe beside its label, a confirmation when the
// click is out of turn (the last probe is past 30 minutes but under a day old), and the
// answer's own words when the budget held it. The button only asks the route to check; the
// route's budget decides whether @SpamBot is asked.
export function SpamCheckControl({
  account,
  onResult,
}: {
  account: AccountRead;
  onResult: (result: SpamCheckResult | null) => void;
}) {
  const { t, i18n } = useTranslation();
  const [check, setCheck] = useState<CheckState>('idle');
  const [confirming, setConfirming] = useState(false);
  const tipId = useId();
  const queryClient = useQueryClient();
  const later = useClearedTimeouts();
  // `inlineError`: a refusal is answered in place (the confirmation below, or the toast here),
  // not by the global toast on top of it.
  const spamMutation = useMutation({ ...spamCheckAccountMutation(), meta: { inlineError: true } });

  const lastProbe = instantMs(account.spam_last_probe_at);
  const age = lastProbe === null ? null : formatRelativeTo(lastProbe, Date.now(), i18n.language);

  const send = (confirmed: boolean): Promise<void> => {
    setCheck('loading');
    onResult(null);
    return spamMutation
      .mutateAsync({
        path: { account_id: account.account_id },
        ...(confirmed ? { body: { confirm_out_of_turn: true } } : {}),
      })
      .then((result) => {
        invalidateAccountViews(queryClient);
        // A check can close the appeal or move its re-check: the card's own read is stale now.
        invalidateSpamAppeal(queryClient, account.account_id);
        if (isHeld(result)) {
          onResult(result);
          setCheck('idle');
          return;
        }
        setCheck(result.status === 'clean' ? 'ok' : 'err');
        later(() => {
          setCheck('idle');
        }, 2400);
      })
      .catch((error: unknown) => {
        // The flag the row carried went stale (a probe landed since): ask, then send.
        if (errorMessage(error) === NEEDS_CONFIRMATION) {
          setCheck('idle');
          setConfirming(true);
          return;
        }
        setCheck('err');
        toastError(mutationErrorText(error));
      });
  };

  const onClick = () => {
    if (account.spam_out_of_turn) {
      setConfirming(true);
      return;
    }
    void send(false);
  };

  return (
    <>
      <span className="tb-tip">
        <Button
          size="sm"
          aria-describedby={tipId}
          onClick={onClick}
          loading={check === 'loading'}
          className={`gap-2 rounded-full ${
            check === 'ok'
              ? 'border-success bg-success-deep text-on-fill hover:border-success'
              : check === 'err'
                ? 'border-danger bg-danger text-on-fill hover:border-danger'
                : 'text-content-muted'
          }`}
        >
          {check === 'ok' && (
            <span className="tb-blur inline-flex">
              <Icon name="check" size={14} className="stroke-on-fill" />
            </span>
          )}
          {check === 'err' && (
            <span className="tb-blur inline-flex">
              <Icon name="close" size={14} className="stroke-on-fill" />
            </span>
          )}
          <span>{t('accounts.edit.signalsCheck')}</span>
          {age !== null && (
            <span className="type-small text-content-subtle" data-testid="spam-last-probe">
              {age}
            </span>
          )}
        </Button>
        <span id={tipId} role="tooltip" className="tb-tip-pop">
          {age === null
            ? t('accounts.edit.signalsTip')
            : `${t('accounts.edit.signalsTip')} · ${t('accounts.edit.spamCard.tipAge', { when: age })}`}
        </span>
      </span>
      {confirming ? (
        <ConfirmModal
          title={t('accounts.edit.spamCard.confirmTitle')}
          body={t('accounts.edit.spamCard.confirmBody', { when: age ?? '—' })}
          confirmLabel={t('accounts.edit.spamCard.confirmYes')}
          cancelLabel={t('accounts.edit.spamCard.confirmCancel')}
          onClose={() => {
            setConfirming(false);
          }}
          onConfirm={() => send(true)}
        />
      ) : null}
    </>
  );
}
