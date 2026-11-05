import { useMutation } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { errorMessage, errorRetryAfterSeconds } from '@/shared/lib';
import { Button, useModalDirty } from '@/shared/ui';

import { requestLoginCodeMutation, submitLoginCodeMutation } from '@/entities/account';

// Seconds before "send a new code" unlocks after a send: Telegram throttles code
// requests, and a tap-happy retry only earns a flood wait. A flood wait the server
// reports replaces it with the real remaining time.
const RESEND_COOLDOWN_SECONDS = 30;

// The stable codes the backend answers a request/submit with; anything else reads
// as the generic failure.
const KNOWN_CODES = new Set([
  'no_pending_code',
  'code_expired',
  'code_invalid',
  'password_needed',
  'password_invalid',
  'flood_wait',
  'unavailable',
  'phone_invalid',
  'phone_banned',
]);

// These end the pending challenge: the only way forward is a new code.
const CHALLENGE_GONE = new Set(['no_pending_code', 'code_expired']);

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
  const [sent, setSent] = useState(false);
  const [waitSeconds, setWaitSeconds] = useState(0);
  const passwordRef = useRef<HTMLInputElement>(null);
  // A typed code is the one thing this step cannot get back: closing asks first.
  useModalDirty(code.trim() !== '' || password !== '');
  // Both failures are worded in place from the backend's stable code (a toast would
  // show the raw code beside it).
  const requestCode = useMutation({ ...requestLoginCodeMutation(), meta: { inlineError: true } });
  const submitCode = useMutation({ ...submitLoginCodeMutation(), meta: { inlineError: true } });

  useEffect(() => {
    if (waitSeconds <= 0) return;
    const timer = setTimeout(() => {
      setWaitSeconds((seconds) => seconds - 1);
    }, 1000);
    return () => {
      clearTimeout(timer);
    };
  }, [waitSeconds]);

  const errorText = (error: unknown): string => {
    const message = errorMessage(error);
    const key = message && KNOWN_CODES.has(message) ? message : 'failed';
    return t(`accounts.addWizard.codeErrors.${key}`, {
      seconds: errorRetryAfterSeconds(error) ?? '?',
    });
  };

  const sendCode = () => {
    if (!accountId) return;
    submitCode.reset();
    requestCode.mutate(
      { path: { account_id: accountId } },
      {
        onSuccess: () => {
          setSent(true);
          setCode('');
          setWaitSeconds(RESEND_COOLDOWN_SECONDS);
        },
        onError: (error) => {
          setWaitSeconds(errorRetryAfterSeconds(error) ?? 0);
        },
      },
    );
  };

  const onConfirmLogin = () => {
    if (!accountId) return;
    submitCode.mutate(
      {
        path: { account_id: accountId },
        // The code is trimmed (a spaced SMS code); the password goes verbatim —
        // a cloud password may legitimately start or end with a space.
        body: { code: code.trim(), password: password || null },
      },
      {
        onSuccess: onDone,
        onError: (error) => {
          const message = errorMessage(error) ?? '';
          if (CHALLENGE_GONE.has(message)) setWaitSeconds(0);
          if (message === 'password_needed') passwordRef.current?.focus();
        },
      },
    );
  };

  const needsPassword = errorMessage(submitCode.error) === 'password_needed';
  const resendLabel =
    waitSeconds > 0
      ? t('accounts.addWizard.resendIn', { seconds: waitSeconds })
      : t('accounts.addWizard.resendCode');

  return (
    <>
      {!sent ? (
        <div className="flex flex-col gap-3">
          <div className="rounded-md border border-line bg-surface-card px-4 py-4 type-body text-content-subtle">
            {phone}
          </div>
          <Button
            variant="primary"
            className="self-start"
            onClick={sendCode}
            disabled={requestCode.isPending || !accountId || waitSeconds > 0}
          >
            {requestCode.isPending
              ? t('accounts.addWizard.sending')
              : waitSeconds > 0
                ? resendLabel
                : t('accounts.addWizard.sendCode')}
          </Button>
          {requestCode.isError && (
            <div className="type-body text-danger">{errorText(requestCode.error)}</div>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="rounded-md bg-success-tint px-3 py-3 text-body font-medium text-success-deep">
            {t('accounts.addWizard.codeSent', { phone })}
          </div>
          <label className="block type-small-medium">
            {t('accounts.addWizard.smsCode')}
            <input
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={(event) => {
                setCode(event.target.value);
              }}
              className="mt-2 w-full rounded-md border border-line bg-surface-card px-3 py-3 text-body font-normal text-content-primary outline-hidden focus:border-action-primary"
            />
          </label>
          <label className="block type-small-medium">
            {t('accounts.addWizard.twoFA')}
            <input
              ref={passwordRef}
              type="password"
              // `off` is documented as ignored on password inputs;
              // `new-password` is the token that actually suppresses the
              // fill of the operator's own saved credential.
              autoComplete="new-password"
              aria-invalid={needsPassword || undefined}
              value={password}
              onChange={(event) => {
                setPassword(event.target.value);
              }}
              className="mt-2 w-full rounded-md border border-line bg-surface-card px-3 py-3 text-body font-normal text-content-primary outline-hidden focus:border-action-primary aria-invalid:border-danger"
            />
          </label>
          {submitCode.isError && (
            <div className="type-body text-danger" role="alert">
              {errorText(submitCode.error)}
            </div>
          )}
          {requestCode.isError && (
            <div className="type-body text-danger">{errorText(requestCode.error)}</div>
          )}
          <Button
            variant="secondary"
            className="self-start"
            onClick={sendCode}
            disabled={requestCode.isPending || waitSeconds > 0 || !accountId}
          >
            {requestCode.isPending ? t('accounts.addWizard.sending') : resendLabel}
          </Button>
        </div>
      )}
      <div className="mt-6 flex justify-end gap-2">
        <Button
          variant="primary"
          onClick={onConfirmLogin}
          disabled={!code.trim() || !sent || submitCode.isPending}
        >
          {t('accounts.addWizard.confirmLogin')}
        </Button>
      </div>
    </>
  );
}
