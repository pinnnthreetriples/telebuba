import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, ConfirmModal, toastError } from '@/shared/ui';

import {
  invalidateAccountViews,
  markSpamAppealFiledMutation,
  openSpamAppealWindowMutation,
  spamAppealQueryKey,
  spamAppealQueryOptions,
} from '@/entities/account';
import type { AccountRead } from '@/shared/api';
import { formatLocalDateTime, mutationErrorText } from '@/shared/lib';

import { appealView, instantMs, spamVerdictOf } from '../model/spamCard';

// The semi-automatic appeal of a spam block (docs/spam-intel.md 5.12). A HUMAN does the whole
// appeal in the account's own Telegram Web window. These buttons only open that window on
// the @SpamBot dialog and record that the operator says the appeal was filed: nothing here
// sends text, presses a bot button or fills a field, and there is no input on this block.
export function SpamAppealControls({ account }: { account: AccountRead }) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const accountId = account.account_id;
  const limited = spamVerdictOf(account) === 'limited';
  // The re-check time is only on the appeal's own read; fetch it for a block that has one.
  const watching = limited || (account.spam_appeal_status ?? 'none') !== 'none';
  const state = useQuery({
    ...spamAppealQueryOptions({ path: { account_id: accountId } }),
    enabled: watching,
  });
  const openWindow = useMutation(openSpamAppealWindowMutation());
  const markFiled = useMutation({ ...markSpamAppealFiledMutation(), meta: { inlineError: true } });
  const [confirming, setConfirming] = useState(false);
  const view = appealView(account, state.data);
  const noProxy = account.proxy_id == null;
  const at = (iso: string | null) => {
    const ms = instantMs(iso);
    return ms === null ? '—' : formatLocalDateTime(ms, i18n.language);
  };

  if (view.kind === 'hidden') return null;

  const onOpen = () => {
    openWindow.mutate(
      { path: { account_id: accountId } },
      {
        onSuccess: (result) => {
          // A 200 is a window, not a login: the operator may have to finish signing in there.
          if (!result.signed_in) {
            toastError(
              t(
                result.reason
                  ? `accounts.openWeb.reason.${result.reason}`
                  : 'accounts.openWeb.notSignedIn',
              ),
            );
          }
        },
      },
    );
  };

  const onFiled = (): Promise<void> =>
    markFiled.mutateAsync({ path: { account_id: accountId } }).then(
      (filed) => {
        queryClient.setQueryData(spamAppealQueryKey({ path: { account_id: accountId } }), filed);
        invalidateAccountViews(queryClient);
      },
      (error: unknown) => {
        toastError(mutationErrorText(error));
      },
    );

  return (
    <div className="mt-3 flex flex-col gap-2" data-testid="spam-appeal">
      <div className="type-body-medium text-content-primary">
        {t('accounts.edit.spamCard.appeal.title')}
      </div>
      {view.kind === 'available' && (
        <>
          <p className="m-0 type-small text-content-subtle">
            {t('accounts.edit.spamCard.appeal.hint')}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="primary"
              disabled={noProxy}
              loading={openWindow.isPending}
              onClick={onOpen}
            >
              {t('accounts.edit.spamCard.appeal.open')}
            </Button>
            <Button
              size="sm"
              loading={markFiled.isPending}
              onClick={() => {
                setConfirming(true);
              }}
            >
              {t('accounts.edit.spamCard.appeal.filed')}
            </Button>
          </div>
          {noProxy && (
            <p className="m-0 type-small text-warning-deep" role="note">
              {t('accounts.edit.spamCard.appeal.noProxy')}
            </p>
          )}
        </>
      )}
      {view.kind === 'filed' && (
        <>
          <p className="m-0 type-body text-content-primary">
            {t('accounts.edit.spamCard.appeal.filedBy', {
              when: at(view.filedAt),
              who: view.filedBy ?? '—',
            })}
          </p>
          {view.recheckAt !== null && (
            <p className="m-0 type-small text-content-subtle">
              {t('accounts.edit.spamCard.appeal.recheck', { when: at(view.recheckAt) })}
            </p>
          )}
          <p className="m-0 type-small text-content-subtle">
            {t('accounts.edit.spamCard.appeal.hint')}
          </p>
        </>
      )}
      {view.kind === 'closed' && (
        <p className="m-0 type-small text-content-subtle">
          {t('accounts.edit.spamCard.appeal.closed', {
            when: at(view.filedAt),
            who: view.filedBy ?? '—',
          })}
        </p>
      )}
      {confirming ? (
        <ConfirmModal
          title={t('accounts.edit.spamCard.appeal.confirmTitle')}
          body={t('accounts.edit.spamCard.appeal.confirmBody')}
          confirmLabel={t('accounts.edit.spamCard.appeal.confirmYes')}
          cancelLabel={t('accounts.edit.spamCard.appeal.confirmCancel')}
          onClose={() => {
            setConfirming(false);
          }}
          onConfirm={onFiled}
        />
      ) : null}
    </div>
  );
}
