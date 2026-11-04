import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  beginQrLoginMutation,
  qrLoginQueryKey,
  qrLoginQueryOptions,
  submitQrLoginPasswordMutation,
} from '@/entities/account';
import type { QrLoginView } from '@/shared/api';
import { errorMessage } from '@/shared/lib';
import { Button, Spinner, useModalDirty } from '@/shared/ui';

// How often the step re-reads the sign-in while it waits for a scan or a password.
const POLL_MS = 2000;
const KNOWN_ERRORS = new Set([
  'unknown_account',
  'already_signed_in',
  'no_qr_login',
  'not_awaiting_password',
  'account_exists',
  'password_invalid',
  'flood_wait',
  'connection_failed',
]);
// Codes after which asking for a new QR cannot help: the account is gone or taken.
const FINAL_ERRORS = new Set(['unknown_account', 'already_signed_in', 'account_exists']);

// The add-wizard's step for the QR method: show the QR Telegram issued for this
// account, wait for the operator to scan it from a signed-in phone, ask for the
// cloud password when the account has one, and move on once it is saved. Nothing
// starts on mount (StrictMode would open and drop the sign-in twice); the operator
// clicks, like the phone method's "send code".
export function QrLoginStep({
  accountId,
  onDone,
}: {
  accountId: string | null;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [password, setPassword] = useState('');
  const [begun, setBegun] = useState(false);
  // A typed password is the one thing this step cannot get back: closing asks first.
  useModalDirty(password !== '');
  const pathOptions = { path: { account_id: accountId ?? '' } };
  // Every answer is also written into the polled query, so the newest one wins
  // whichever request produced it (a restart must not be shadowed by a stale read).
  const publish = (view: QrLoginView) => {
    queryClient.setQueryData(qrLoginQueryKey(pathOptions), view);
  };
  const begin = useMutation({
    ...beginQrLoginMutation(),
    meta: { inlineError: true },
    onSuccess: (view) => {
      setBegun(true);
      publish(view);
    },
  });
  const submit = useMutation({
    ...submitQrLoginPasswordMutation(),
    meta: { inlineError: true },
    onSuccess: publish,
  });
  const poll = useQuery({
    ...qrLoginQueryOptions(pathOptions),
    enabled: begun && accountId !== null,
    retry: false,
    refetchInterval: (query) => {
      // A failed read keeps the last good data; polling on would only repeat it.
      if (query.state.error) return false;
      const status = query.state.data?.status;
      return status === 'waiting' || status === 'password_needed' ? POLL_MS : false;
    },
  });
  // A refused request hides the last good answer: its QR or form no longer applies.
  const requestError = begin.error ?? poll.error ?? submit.error;
  const view = requestError ? undefined : (poll.data ?? begin.data);

  // The saved account is what the later steps act on; tell the wizard once.
  const reported = useRef(false);
  useEffect(() => {
    if (view?.status === 'alive' && !reported.current) {
      reported.current = true;
      onDone();
    }
  }, [view?.status, onDone]);

  const startOrRestart = () => {
    if (!accountId) return;
    submit.reset();
    setPassword('');
    begin.mutate({ path: { account_id: accountId } });
  };
  const sendPassword = () => {
    if (!accountId || !password) return;
    submit.mutate({ path: { account_id: accountId }, body: { password } });
  };

  const errorText = (code: string | undefined | null, seconds?: number | null): string =>
    t(`accounts.addWizard.qrErrors.${code && KNOWN_ERRORS.has(code) ? code : 'generic'}`, {
      seconds: seconds ?? '?',
    });
  const requestCode = requestError ? errorMessage(requestError) : undefined;
  const failedCode = view?.status === 'failed' ? view.error_code : undefined;
  const alertText = requestError
    ? errorText(requestCode)
    : view?.status === 'failed'
      ? errorText(failedCode, view.flood_wait_seconds)
      : null;
  const canRetry = !FINAL_ERRORS.has(requestCode ?? failedCode ?? '');

  if (view?.status === 'alive') {
    return (
      <div
        role="status"
        className="rounded-md bg-success-tint px-3 py-3 text-body font-medium text-success-deep"
      >
        {t('accounts.addWizard.loginOk')}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {view?.status === 'waiting' && view.qr_image ? (
        <>
          <div className="flex justify-center">
            <img
              src={view.qr_image}
              alt={t('accounts.addWizard.qrAlt')}
              className="w-confirm max-w-full rounded-md border border-line"
            />
          </div>
          <div className="type-body text-content-subtle">
            {t('accounts.addWizard.qrInstructions')}
          </div>
          <div className="type-small text-content-muted">
            {t('accounts.addWizard.qrAutoRefresh')}
          </div>
        </>
      ) : view?.status === 'password_needed' ? (
        <div className="flex flex-col gap-3">
          <div className="rounded-md bg-info-tint px-3 py-3 type-body">
            {t('accounts.addWizard.qrPasswordHint')}
          </div>
          <label className="block type-small-medium">
            {t('accounts.addWizard.qrPasswordLabel')}
            <input
              type="password"
              // `new-password` is the token that actually suppresses the fill of the
              // operator's own saved credential (`off` is ignored on password inputs).
              autoComplete="new-password"
              value={password}
              onChange={(event) => {
                setPassword(event.target.value);
              }}
              className="mt-2 w-full rounded-md border border-line bg-surface-card px-3 py-3 text-body font-normal text-content-primary outline-hidden focus:border-action-primary"
            />
          </label>
          {view.error_code === 'password_invalid' && !submit.isPending && (
            <div role="alert" className="type-small text-danger-deep">
              {errorText('password_invalid')}
            </div>
          )}
          <Button
            variant="primary"
            className="self-start"
            onClick={sendPassword}
            disabled={!password || submit.isPending}
          >
            {t('accounts.addWizard.qrPasswordSubmit')}
          </Button>
        </div>
      ) : view?.status === 'expired' ? (
        <div className="flex flex-col gap-3">
          <div className="type-body">{t('accounts.addWizard.qrExpired')}</div>
          <Button
            variant="primary"
            className="self-start"
            onClick={startOrRestart}
            disabled={begin.isPending}
          >
            {t('accounts.addWizard.qrRefresh')}
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="type-body text-content-subtle">
            {t('accounts.addWizard.qrInstructions')}
          </div>
          {begin.isPending ? (
            <div role="status" className="flex items-center gap-3 type-body">
              <Spinner />
              {t('accounts.addWizard.qrShowing')}
            </div>
          ) : (
            canRetry && (
              <Button
                variant="primary"
                className="self-start"
                onClick={startOrRestart}
                disabled={!accountId}
              >
                {view?.status === 'failed' || requestError
                  ? t('accounts.addWizard.qrRetry')
                  : t('accounts.addWizard.qrShow')}
              </Button>
            )
          )}
        </div>
      )}
      {alertText && (
        <div role="alert" className="type-small text-danger-deep">
          {alertText}
        </div>
      )}
    </div>
  );
}
