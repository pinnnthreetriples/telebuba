import { useMutation } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  cancelAccountTwofaEmailMutation,
  clearAccountTwofaEmailMutation,
  confirmAccountTwofaEmailMutation,
  resendAccountTwofaEmailMutation,
  setAccountTwofaEmailMutation,
} from '@/entities/account';
import { ConfirmModal } from '@/shared/ui';

import { Spinner } from './_shared';
import { FIELD, LABEL } from './_styles';

// The recovery-email leg of the 2FA card: attach an address, then type the code
// Telegram mailed. Rendered only when the account has a password AND that
// password is stored here, because the backend can authorise neither otherwise.
//
// The override is the pending address: `undefined` means "read the state off the
// status"; `null` means "the write just told us there is no pending address any
// more" — which matters because the status refetch has not landed yet at that
// point. The parent keys this component on the server-side state, so an override
// lives exactly until the server confirms it.
//
// `codeLength` therefore CANNOT live here. It exists only in the attach response,
// and the refetch that response triggers flips the key (the pattern appears), which
// remounts this component and would drop it — so the parent holds it and passes it
// back down. The number is plumbed through three backend layers precisely so the
// operator gets the right field length; losing it on the first refetch would waste
// all of that.
export function TwoFactorEmail({
  accountId,
  hasRecovery,
  unconfirmedPattern,
  codeLength,
  onCodeLength,
  onChanged,
}: {
  accountId: string;
  hasRecovery: boolean;
  unconfirmedPattern: string | null;
  codeLength: number | null;
  onCodeLength: (length: number | null) => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [address, setAddress] = useState('');
  const [code, setCode] = useState('');
  const [override, setOverride] = useState<string | null | undefined>(undefined);
  // One modal serves both destructive branches; the value is the i18n key suffix.
  // The HANDLERS stay separate because the two call different endpoints.
  const [confirming, setConfirming] = useState<'Unlink' | 'Cancel' | null>(null);
  const codeRef = useRef<HTMLInputElement>(null);

  const setEmail = useMutation(setAccountTwofaEmailMutation());
  const confirmEmail = useMutation(confirmAccountTwofaEmailMutation());
  const resendEmail = useMutation(resendAccountTwofaEmailMutation());
  const cancelEmail = useMutation(cancelAccountTwofaEmailMutation());
  const clearEmail = useMutation(clearAccountTwofaEmailMutation());

  const path = { path: { account_id: accountId } } as const;
  const pending: string | null = override === undefined ? unconfirmedPattern : override;

  const onAttach = () => {
    const email = address.trim();
    setEmail.mutate(
      { ...path, body: { email } },
      {
        onSuccess: (result) => {
          // Driven off the RESPONSE: Telegram has already mailed the code, so
          // the operator gets the code field immediately instead of waiting a
          // round trip to be told what they just did.
          setOverride(result.pending ? email : null);
          onCodeLength(result.pending ? (result.code_length ?? null) : null);
          setAddress('');
          onChanged();
        },
      },
    );
  };

  const onConfirmCode = () => {
    confirmEmail.mutate(
      { ...path, body: { code: code.trim() } },
      {
        onSuccess: () => {
          setOverride(null);
          onCodeLength(null);
          setCode('');
          onChanged();
        },
        // A wrong or expired code is named by the global toast; the field keeps
        // what was typed and takes focus back so the retry is one keystroke, not
        // a re-run of the whole two-request flow.
        onError: () => {
          codeRef.current?.focus();
        },
      },
    );
  };

  // Nothing to override: a resend changes no state the card renders, and the
  // response repeats no code length (that number exists only in the attach reply).
  const onResend = () => {
    resendEmail.mutate(path);
  };

  // `cancel` abandons a PENDING verification; `clear` detaches a CONFIRMED address.
  // Two different Telegram calls, so two different routes — see TwoFactorEmail's
  // confirmed branch and the `_accounts_twofa` route docstrings.
  const onDrop = () =>
    cancelEmail.mutateAsync(path).then(() => {
      setOverride(null);
      onCodeLength(null);
      setCode('');
      onChanged();
    });

  // No local state to reset: the parent keys this component on the server-side
  // email state, so the refetch `onChanged` triggers remounts it.
  const onUnlink = () => clearEmail.mutateAsync(path).then(onChanged);

  return (
    <div className="mt-3 border-t border-[#f0eeeb] pt-3">
      {/* Both rows when Telegram reports both, and the pending one is NOT hidden
          behind the confirmed one. Telegram answers with a confirmed address and a
          freshly pending one whenever the operator swaps the recovery address from
          the app — and while `has_recovery` won that test, the code field never
          appeared, so the swap could not be completed from here at all. */}
      {hasRecovery ? (
        <div className={`flex items-center justify-between gap-3 ${pending ? 'mb-[10px]' : ''}`}>
          <span className="text-[12.5px] text-ink-muted">
            {t('accounts.edit.twofaRecovery')}: {t('accounts.edit.twofaRecoveryOn')}
          </span>
          <button
            type="button"
            onClick={() => {
              setConfirming('Unlink');
            }}
            className="bg-transparent p-0 text-[12.5px] font-medium text-danger"
          >
            {t('accounts.edit.twofaEmailUnlink')}
          </button>
        </div>
      ) : null}
      {pending ? (
        <>
          <div className="mb-[10px] text-[12.5px] text-ink-muted">
            {t('accounts.edit.twofaEmailSent', { pattern: pending })}
          </div>
          <label className="mb-[10px] block">
            <span className={LABEL}>{t('accounts.edit.twofaEmailCode')}</span>
            <input
              ref={codeRef}
              value={code}
              onChange={(event) => {
                setCode(event.target.value);
              }}
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={codeLength ?? undefined}
              className={`${FIELD} font-mono tracking-[0.18em]`}
            />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={onConfirmCode}
              disabled={confirmEmail.isPending || !code.trim()}
              className="rounded-[10px] border border-line-input bg-white px-4 py-[7px] text-[12.5px] font-medium disabled:opacity-50"
            >
              {confirmEmail.isPending ? (
                <Spinner size={13} />
              ) : (
                t('accounts.edit.twofaEmailConfirm')
              )}
            </button>
            <button
              type="button"
              onClick={onResend}
              disabled={resendEmail.isPending}
              className="rounded-[8px] border border-line-input bg-white px-3 py-[5px] text-[12px] font-medium text-ink-muted disabled:opacity-50"
            >
              {resendEmail.isPending ? <Spinner size={12} /> : t('accounts.edit.twofaEmailResend')}
            </button>
            <button
              type="button"
              onClick={() => {
                setConfirming('Cancel');
              }}
              className="bg-transparent p-0 text-[12.5px] font-medium text-danger"
            >
              {t('accounts.edit.twofaEmailCancel')}
            </button>
          </div>
        </>
      ) : hasRecovery ? null : (
        <>
          <div className="mb-[10px] text-[12.5px] text-ink-muted">
            {t('accounts.edit.twofaRecovery')}: {t('accounts.edit.twofaRecoveryOff')}
          </div>
          <label className="mb-[6px] block">
            <span className={LABEL}>{t('accounts.edit.twofaEmailAddress')}</span>
            <input
              value={address}
              onChange={(event) => {
                setAddress(event.target.value);
              }}
              type="email"
              autoComplete="off"
              className={FIELD}
            />
          </label>
          <div className="mb-[12px] text-[11.5px] text-ink-subtle">
            {t('accounts.edit.twofaEmailWarn')}
          </div>
          <button
            type="button"
            onClick={onAttach}
            disabled={setEmail.isPending || !address.trim()}
            className="w-full rounded-[10px] border border-line-input bg-white py-[9px] text-[13px] font-medium disabled:opacity-50"
          >
            {setEmail.isPending ? <Spinner size={14} /> : t('accounts.edit.twofaEmailAttach')}
          </button>
        </>
      )}
      {confirming ? (
        <ConfirmModal
          title={t(`accounts.edit.twofaEmail${confirming}Title`)}
          body={t(`accounts.edit.twofaEmail${confirming}Body`)}
          confirmLabel={t(`accounts.edit.twofaEmail${confirming}Confirm`)}
          cancelLabel={t('accounts.edit.cancel')}
          onClose={() => {
            setConfirming(null);
          }}
          onConfirm={confirming === 'Unlink' ? onUnlink : onDrop}
        />
      ) : null}
    </div>
  );
}
