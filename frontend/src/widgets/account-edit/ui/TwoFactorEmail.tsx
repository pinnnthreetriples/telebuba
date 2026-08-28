import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountTwofaQueryKey,
  cancelAccountTwofaEmailMutation,
  clearAccountTwofaEmailMutation,
  confirmAccountTwofaEmailMutation,
  resendAccountTwofaEmailMutation,
  setAccountTwofaEmailMutation,
} from '@/entities/account';
import { Button, ConfirmModal, Input } from '@/shared/ui';

import { Spinner } from './_shared';
import { LABEL } from './_styles';

// The exact bounds `schemas/twofa` enforces. Gating on anything looser makes the
// button fire a request that can only 422, and a `validation_error` envelope
// resolves through no `shell.code.*` entry — so the operator would get FastAPI prose
// or the generic fallback instead of an inline message they can act on.
const EMAIL_SHAPE = /.+@.+/;
const MAX_EMAIL_LENGTH = 254;
// Telegram's real length comes back as `code_length` in the attach reply only; this
// is the server's upper bound, and it is what the field falls back to after a reload
// or a reopened card, where the pattern came from the status and the length is gone.
const MAX_CODE_LENGTH = 32;

// The recovery-email leg of the 2FA card: attach an address, then type the code
// Telegram mailed. Mounted whenever the live read says the account has a password —
// with or without our copy of it, see `hasStored` — and, when the read failed but a
// pending address is known, by that branch too, because finishing a verification
// already under way needs neither the read nor the password.
//
// The override is the pending address: `undefined` means "read the state off the
// status"; `null` means "the write just told us there is no pending address any
// more" — which matters because the status refetch has not landed yet at that
// point. The parent keys this component on the server-side state, so an override
// lives exactly until the server confirms it.
//
// `codeLength` therefore CANNOT live here, and neither can the typed `code`: both
// exist across a refetch that flips the key (the pattern appears or disappears) and
// remounts this component. The parent holds them and passes them back down. The
// length is plumbed through three backend layers precisely so the operator gets the
// right field size, and the code is what they just read out of the letter — the
// refetch after an attach wiped it here, so a code typed promptly was silently
// blanked and the POST went out empty.
export function TwoFactorEmail({
  accountId,
  hasRecovery,
  hasStored,
  unconfirmedPattern,
  code,
  onCode,
  codeLength,
  onCodeLength,
  onChanged,
}: {
  accountId: string;
  // Three-valued: `null` is "the live read failed, so whether a confirmed address
  // exists is not knowable" and prints no row at all. The read-error branch of the
  // parent mounts this leg with it, to keep a pending verification finishable without
  // claiming anything about a confirmed one.
  hasRecovery: boolean | null;
  // Whether THIS dashboard still holds the account's current password. Only the two
  // authorised operations need it — attaching an address (`updatePasswordSettings`)
  // and detaching a confirmed one (the same call with an empty email). Confirming a
  // code, re-sending it and cancelling a pending address need no password at all, so
  // gating the whole leg on this hid a pending verification the operator could still
  // have completed.
  hasStored: boolean;
  unconfirmedPattern: string | null;
  code: string;
  onCode: (code: string) => void;
  codeLength: number | null;
  onCodeLength: (length: number | null) => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [address, setAddress] = useState('');
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
  const email = address.trim();
  const addressValid = EMAIL_SHAPE.test(email) && email.length <= MAX_EMAIL_LENGTH;

  const onAttach = () => {
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
          // The code belongs to the address it was typed for. It is lifted to the
          // parent so a refetch cannot wipe it, and the cost of that is exactly this:
          // a code left over from an abandoned pending address prefilled the field for
          // a NEW one, with Confirm enabled over something that can only be refused.
          onCode('');
          onChanged();
        },
      },
    );
  };

  const onConfirmCode = () => {
    confirmEmail.mutate(
      { ...path, body: { code: code.trim() } },
      {
        onSuccess: (view) => {
          // Rendered from the RESPONSE, which is a whole fresh AccountTwoFactorView
          // (the route re-reads the live state for exactly this reason). Nulling the
          // override and waiting for the refetch instead left `hasRecovery` at its
          // stale `false` for one live `account.getPassword` round trip, and the card
          // spent those seconds saying the address is not attached and offering to
          // attach the one just confirmed.
          queryClient.setQueryData(accountTwofaQueryKey(path), view);
          setOverride(null);
          onCodeLength(null);
          onCode('');
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
      onCode('');
      onChanged();
    });

  // Rendered from the RESPONSE, for the reason the confirm path gives: `clear` answers
  // with a whole fresh AccountTwoFactorView, and discarding it in favour of the
  // refetch left `hasRecovery` at its stale `true` for one live `account.getPassword`
  // round trip — the card kept saying the address is attached and kept the Detach
  // button live, where a second click fires a `clear` that can only refuse.
  //
  // No local state to reset: the parent keys this component on the server-side email
  // state, so writing that state remounts it.
  const onUnlink = () =>
    clearEmail.mutateAsync(path).then((view) => {
      queryClient.setQueryData(accountTwofaQueryKey(path), view);
      onChanged();
    });

  return (
    <div className="mt-md border-t border-line-row pt-md">
      {/* Both rows when Telegram reports both, and the pending one is NOT hidden
          behind the confirmed one. Telegram answers with a confirmed address and a
          freshly pending one whenever the operator swaps the recovery address from
          the app — and while `has_recovery` won that test, the code field never
          appeared, so the swap could not be completed from here at all. */}
      {hasRecovery === null ? null : (
        // The one place the recovery state is stated. It used to be said here AND in a
        // summary row the parent rendered just above, which read as a duplicate; the
        // row moved here rather than going, so both states stay reachable — including
        // "not attached" while a verification is pending, which is where the parent's
        // row used to be the only one.
        <div
          className={`flex items-center justify-between gap-md ${
            pending || !hasRecovery ? 'mb-md' : ''
          }`}
        >
          <span className="type-prose">
            {t('accounts.edit.twofaRecovery')}:{' '}
            {hasRecovery ? t('accounts.edit.twofaRecoveryOn') : t('accounts.edit.twofaRecoveryOff')}
          </span>
          {hasRecovery ? (
            <button
              type="button"
              onClick={() => {
                setConfirming('Unlink');
              }}
              disabled={!hasStored}
              className="bg-transparent p-0 text-body font-medium text-danger disabled:opacity-50"
            >
              {t('accounts.edit.twofaEmailUnlink')}
            </button>
          ) : null}
        </div>
      )}
      {pending ? (
        <>
          <div className="mb-md type-prose">
            {t('accounts.edit.twofaEmailSent', { pattern: pending })}
          </div>
          <label className="mb-md block">
            <span className={LABEL}>{t('accounts.edit.twofaEmailCode')}</span>
            <Input
              className="font-mono tracking-code"
              ref={codeRef}
              value={code}
              onChange={(event) => {
                onCode(event.target.value);
              }}
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={codeLength ?? MAX_CODE_LENGTH}
            />
          </label>
          <div className="flex flex-wrap items-center gap-sm">
            <button
              type="button"
              onClick={onConfirmCode}
              disabled={confirmEmail.isPending || !code.trim()}
              className="rounded-lg border border-line bg-surface-card px-lg py-sm text-body font-medium disabled:opacity-50"
            >
              {confirmEmail.isPending ? (
                <Spinner size={13} />
              ) : (
                t('accounts.edit.twofaEmailConfirm')
              )}
            </button>
            <Button
              size="xs"
              className="text-content-muted"
              onClick={onResend}
              loading={resendEmail.isPending}
            >
              {resendEmail.isPending ? <Spinner size={12} /> : t('accounts.edit.twofaEmailResend')}
            </Button>
            <button
              type="button"
              onClick={() => {
                setConfirming('Cancel');
              }}
              className="bg-transparent p-0 text-body font-medium text-danger"
            >
              {t('accounts.edit.twofaEmailCancel')}
            </button>
          </div>
        </>
      ) : hasRecovery === false ? (
        <>
          {/* Only `false` invites an attach. `null` is "we could not read whether one
              is attached", and offering to attach one over that is a guess. */}
          <label className="mb-tight block">
            <span className={LABEL}>{t('accounts.edit.twofaEmailAddress')}</span>
            <Input
              value={address}
              onChange={(event) => {
                setAddress(event.target.value);
              }}
              type="email"
              autoComplete="off"
              maxLength={MAX_EMAIL_LENGTH}
            />
            {email && !addressValid ? (
              <span className="mt-tight block type-caption font-medium text-danger">
                {t('accounts.edit.twofaEmailErrShape')}
              </span>
            ) : null}
          </label>
          <div className="mb-md type-caption">{t('accounts.edit.twofaEmailWarn')}</div>
          <Button
            size="block"
            onClick={onAttach}
            disabled={setEmail.isPending || !addressValid || !hasStored}
          >
            {setEmail.isPending ? <Spinner size={14} /> : t('accounts.edit.twofaEmailAttach')}
          </Button>
        </>
      ) : null}
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
