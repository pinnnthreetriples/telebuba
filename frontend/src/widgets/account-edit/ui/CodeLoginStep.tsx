import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/shared/ui';

import { requestLoginCodeMutation, submitLoginCodeMutation } from '@/entities/account';

// The add-wizard's final step for the phone method: request the Telegram login
// code, then confirm it (with the optional 2FA password). Extracted from
// AddAccountModal — `code`/`password` and the two mutations are used nowhere
// else in the wizard, and keeping them in the parent put four more pieces of
// state next to the provisioning logic that three bugs already hid in.
export function CodeLoginStep({
  accountId,
  phone,
  onDone,
}: {
  accountId: string | null;
  phone: string;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const requestCode = useMutation(requestLoginCodeMutation());
  const submitCode = useMutation(submitLoginCodeMutation());

  const onConfirmLogin = () => {
    if (!accountId) return;
    submitCode.mutate(
      {
        path: { account_id: accountId },
        body: { code: code.trim(), password: password.trim() || null },
      },
      { onSuccess: onDone },
    );
  };

  return (
    <>
      {!requestCode.isSuccess ? (
        <div className="flex flex-col gap-md">
          <div className="rounded-lg border border-line bg-white px-lg py-lg text-body text-ink-subtle">
            {phone}
          </div>
          <Button
            variant="primary"
            className="self-start"
            onClick={() => {
              if (accountId) {
                requestCode.mutate({ path: { account_id: accountId } });
              }
            }}
            disabled={requestCode.isPending || !accountId}
          >
            {requestCode.isPending
              ? t('accounts.addWizard.sending')
              : t('accounts.addWizard.sendCode')}
          </Button>
          {requestCode.isError && (
            <div className="type-prose text-danger">{t('accounts.addWizard.loginErr')}</div>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-md">
          <div className="rounded-lg bg-success-tint px-md py-md text-body font-medium text-success-deep">
            {t('accounts.addWizard.codeSent', { phone })}
          </div>
          <label className="block type-caption font-medium">
            {t('accounts.addWizard.smsCode')}
            <input
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={(event) => {
                setCode(event.target.value);
              }}
              className="mt-tight w-full rounded-lg border border-line bg-white px-md py-md text-lead font-normal text-ink outline-none focus:border-primary"
            />
          </label>
          <label className="block type-caption font-medium">
            {t('accounts.addWizard.twoFA')}
            <input
              type="password"
              // `off` is documented as ignored on password inputs;
              // `new-password` is the token that actually suppresses the
              // fill of the operator's own saved credential.
              autoComplete="new-password"
              value={password}
              onChange={(event) => {
                setPassword(event.target.value);
              }}
              className="mt-tight w-full rounded-lg border border-line bg-white px-md py-md text-lead font-normal text-ink outline-none focus:border-primary"
            />
          </label>
          {submitCode.isError && (
            <div className="type-prose text-danger">{t('accounts.addWizard.loginErr')}</div>
          )}
        </div>
      )}
      <div className="mt-xl flex justify-end gap-sm">
        <Button
          variant="primary"
          onClick={onConfirmLogin}
          disabled={!code.trim() || !requestCode.isSuccess || submitCode.isPending}
        >
          {t('accounts.addWizard.confirmLogin')}
        </Button>
      </div>
    </>
  );
}
