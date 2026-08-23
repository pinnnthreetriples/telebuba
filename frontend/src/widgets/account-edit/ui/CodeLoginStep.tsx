import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

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
          <div className="rounded-lg border border-line bg-white px-4 py-[14px] text-[12.5px] text-ink-subtle">
            {phone}
          </div>
          <button
            type="button"
            onClick={() => {
              if (accountId) {
                requestCode.mutate({ path: { account_id: accountId } });
              }
            }}
            disabled={requestCode.isPending || !accountId}
            className="self-start rounded-full bg-primary px-[22px] py-[9px] text-[13px] font-semibold text-white disabled:opacity-50"
          >
            {requestCode.isPending
              ? t('accounts.addWizard.sending')
              : t('accounts.addWizard.sendCode')}
          </button>
          {requestCode.isError && (
            <div className="text-[12.5px] text-danger">{t('accounts.addWizard.loginErr')}</div>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-md">
          <div className="rounded-lg bg-success-tint px-3 py-[10px] text-[12.5px] font-medium text-success">
            {t('accounts.addWizard.codeSent', { phone })}
          </div>
          <label className="block text-[11px] font-medium text-ink-subtle">
            {t('accounts.addWizard.smsCode')}
            <input
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={(event) => {
                setCode(event.target.value);
              }}
              className="mt-[6px] w-full rounded-lg border border-line-input bg-white px-3 py-[9px] text-[13px] font-normal text-ink outline-none focus:border-primary"
            />
          </label>
          <label className="block text-[11px] font-medium text-ink-subtle">
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
              className="mt-[6px] w-full rounded-lg border border-line-input bg-white px-3 py-[9px] text-[13px] font-normal text-ink outline-none focus:border-primary"
            />
          </label>
          {submitCode.isError && (
            <div className="text-[12.5px] text-danger">{t('accounts.addWizard.loginErr')}</div>
          )}
        </div>
      )}
      <div className="mt-5 flex justify-end gap-sm">
        <button
          type="button"
          onClick={onConfirmLogin}
          disabled={!code.trim() || !requestCode.isSuccess || submitCode.isPending}
          className="rounded-full bg-primary px-[22px] py-[9px] text-[13px] font-semibold text-white disabled:opacity-50"
        >
          {t('accounts.addWizard.confirmLogin')}
        </button>
      </div>
    </>
  );
}
