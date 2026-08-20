import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountTwofaQueryKey,
  accountTwofaQueryOptions,
  invalidateAccountViews,
  removeAccountTwofaMutation,
} from '@/entities/account';
import type { AccountRead, AccountTwoFactorCreated } from '@/shared/api';
import { useClearedTimeouts } from '@/shared/lib';
import { ConfirmModal } from '@/shared/ui';

import { TwoFactorEmail } from './TwoFactorEmail';
import { TwoFactorForm } from './TwoFactorForm';
import { Section, Spinner } from './_shared';
import { FIELD_LOCKED } from './_styles';

// One live fact row inside the 2FA-on state.
function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-[#f0eeeb] py-[9px]">
      <span className="text-[12.5px] text-ink-muted">{label}</span>
      <span className="text-right text-[12.5px] font-medium text-ink">{value}</span>
    </div>
  );
}

// Cloud-password (2FA) card. The state is a live read from Telegram; the POST
// response is the only place the plaintext ever exists, so it lives in component
// state — no query cache, no storage — and is dropped when the card closes
// (collapsing hides the body, it does not unmount it, hence Section's
// onOpenChange).
export function TwoFactorSection({ account }: { account: AccountRead }) {
  const { t } = useTranslation();
  const accountId = account.account_id;
  const queryClient = useQueryClient();
  const later = useClearedTimeouts();

  const [created, setCreated] = useState<AccountTwoFactorCreated | null>(null);
  // Held here, next to `created`, for the reason TwoFactorEmail's own comment gives:
  // the attach response is the only carrier of the code length, and the refetch it
  // triggers flips that component's key and remounts it.
  const [emailCodeLength, setEmailCodeLength] = useState<number | null>(null);
  const [copied, setCopied] = useState(false);
  const [changing, setChanging] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);

  const twofa = useQuery(accountTwofaQueryOptions({ path: { account_id: accountId } }));
  const removeTwofa = useMutation(removeAccountTwofaMutation());

  const invalidate = () => {
    void queryClient.invalidateQueries({
      queryKey: accountTwofaQueryKey({ path: { account_id: accountId } }),
    });
    invalidateAccountViews(queryClient);
  };

  const status = twofa.data?.status ?? null;
  const readError = twofa.data?.error ?? null;
  const hasPassword = status?.has_password === true;
  const hasStored = twofa.data?.has_stored_password === true;
  const removeKind = hasPassword ? 'Disable' : 'Forget';

  const onCreated = (result: AccountTwoFactorCreated) => {
    setCreated(result);
    setChanging(false);
    invalidate();
  };

  // Establishes the clipboard precedent for this codebase: guarded (happy-dom
  // and any non-secure context have no navigator.clipboard), fire-and-forget,
  // and a rejected write never reaches the render path.
  const copyPassword = (password: string) => {
    const clipboard: Clipboard | undefined = navigator.clipboard;
    if (!clipboard) return;
    void clipboard.writeText(password).catch(() => undefined);
    setCopied(true);
    later(() => {
      setCopied(false);
    }, 2400);
  };

  const onRemove = () =>
    removeTwofa.mutateAsync({ path: { account_id: accountId } }).then(() => {
      setChanging(false);
      invalidate();
    });

  return (
    <>
      <Section
        title={t('accounts.edit.twofa')}
        onOpenChange={(open) => {
          // The plaintext is the operator's only copy, not a value to leave
          // sitting in a card they walked away from.
          if (!open) {
            setCreated(null);
            setCopied(false);
            setChanging(false);
            setEmailCodeLength(null);
          }
        }}
        right={
          twofa.isPending ? (
            <Spinner size={13} />
          ) : (
            <span
              className={`shrink-0 rounded-full px-[10px] py-[3px] text-[11.5px] font-medium ${
                hasPassword ? 'bg-success-tint text-success' : 'bg-canvas text-ink-muted'
              }`}
            >
              {hasPassword ? t('accounts.edit.twofaOn') : t('accounts.edit.twofaOff')}
            </span>
          )
        }
      >
        {twofa.isPending ? (
          <div className="py-2">
            <Spinner size={16} />
          </div>
        ) : created ? (
          <>
            <div className="mb-[10px] text-[12.5px] font-semibold text-ink">
              {t('accounts.edit.twofaCreatedTitle')}
            </div>
            {created.stored === false && created.previous_kept !== true ? (
              // The RPC landed but the DB write did not, so this response is the
              // ONLY copy and change/removal are gone until it is set again. NOT the
              // unconfirmed-change case: nothing failed there, the previous password
              // was kept on purpose and the warning below says so.
              <div className="mb-[10px] rounded-[10px] border border-[#f0c9c5] bg-danger-tint px-3 py-[9px] text-[11.5px] font-medium leading-[1.45] text-danger">
                {t('accounts.edit.twofaStoreFailed')}
              </div>
            ) : null}
            {created.confirmed === false ? (
              // The request was on the wire and only the answer was lost, so
              // Telegram may or may not hold this password. Either way it is the
              // only copy — as loud as the store-failure warning above. On a CHANGE
              // the backend deliberately kept the OLD password stored rather than
              // overwrite a credential known to work, so the operator has two
              // candidates and has to check which one the phone asks for.
              <div className="mb-[10px] rounded-[10px] border border-[#f0c9c5] bg-danger-tint px-3 py-[9px] text-[11.5px] font-medium leading-[1.45] text-danger">
                {created.previous_kept === true
                  ? t('accounts.edit.twofaUnconfirmedChange')
                  : t('accounts.edit.twofaUnconfirmed')}
              </div>
            ) : null}
            <div className="mb-[12px] flex items-center gap-2">
              <input
                readOnly
                value={created.password}
                aria-label={t('accounts.edit.twofaNewPassword')}
                className={`${FIELD_LOCKED} font-mono`}
              />
              <button
                type="button"
                onClick={() => {
                  copyPassword(created.password);
                }}
                className="shrink-0 rounded-[10px] border border-line-input bg-white px-3 py-[9px] text-[12px] font-medium text-ink-muted"
              >
                {copied ? t('accounts.edit.twofaCopied') : t('accounts.edit.twofaCopy')}
              </button>
            </div>
            <button
              type="button"
              onClick={() => {
                setCreated(null);
                setCopied(false);
              }}
              className="w-full rounded-[10px] border border-line-input bg-white py-[9px] text-[13px] font-medium"
            >
              {t('accounts.edit.twofaDone')}
            </button>
          </>
        ) : readError != null || twofa.isError ? (
          // A write against an account whose live state we could not read is a
          // guess, so this branch offers nothing actionable.
          <div className="text-[11.5px] text-danger">
            {t('accounts.edit.twofaReadErr', {
              reason: readError
                ? t(`shell.code.${readError}`, { defaultValue: readError })
                : t('shell.mutationError'),
            })}
          </div>
        ) : hasPassword ? (
          <>
            <Fact
              label={t('accounts.edit.twofaHint')}
              value={status?.hint ?? t('accounts.edit.twofaHintNone')}
            />
            <Fact
              label={t('accounts.edit.twofaRecovery')}
              value={
                status?.has_recovery === true
                  ? t('accounts.edit.twofaRecoveryOn')
                  : t('accounts.edit.twofaRecoveryOff')
              }
            />
            <div
              className={`border-b border-[#f0eeeb] py-[9px] text-[12.5px] font-medium ${
                hasStored ? 'text-ink-muted' : 'text-danger'
              }`}
            >
              {hasStored ? t('accounts.edit.twofaStored') : t('accounts.edit.twofaNotStored')}
            </div>
            {status?.pending_reset_date ? (
              // Somebody is trying to take the account with a password reset.
              <div className="border-b border-[#f0eeeb] py-[9px] text-[12.5px] font-semibold text-danger">
                {t('accounts.edit.twofaResetRequested', {
                  date: status.pending_reset_date.slice(0, 10),
                })}
              </div>
            ) : null}
            {hasStored ? (
              <TwoFactorEmail
                // Keyed on the server-side email state: a write's optimistic
                // override then lives exactly until the status confirms it.
                key={`${String(status?.has_recovery === true)}|${status?.email_unconfirmed_pattern ?? ''}`}
                accountId={accountId}
                hasRecovery={status?.has_recovery === true}
                unconfirmedPattern={status?.email_unconfirmed_pattern ?? null}
                codeLength={emailCodeLength}
                onCodeLength={setEmailCodeLength}
                onChanged={invalidate}
              />
            ) : (
              <div className="mt-3 text-[11.5px] leading-[1.45] text-ink-subtle">
                {t('accounts.edit.twofaNotStoredNote')}
              </div>
            )}
            <div className="mt-4">
              {changing ? (
                <TwoFactorForm
                  accountId={accountId}
                  submitLabel={t('accounts.edit.twofaChange')}
                  initialHint={status?.hint ?? ''}
                  onCreated={onCreated}
                />
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setChanging(true);
                  }}
                  disabled={!hasStored}
                  className="w-full rounded-[10px] border border-line-input bg-white py-[9px] text-[13px] font-medium disabled:opacity-50"
                >
                  {t('accounts.edit.twofaChange')}
                </button>
              )}
            </div>
            <div className="mt-[10px] text-center">
              <button
                type="button"
                onClick={() => {
                  setConfirmRemove(true);
                }}
                disabled={!hasStored}
                className="bg-transparent p-0 text-[12.5px] font-medium text-danger disabled:opacity-50"
              >
                {t('accounts.edit.twofaDisable')}
              </button>
            </div>
          </>
        ) : (
          <>
            {hasStored ? (
              // Telegram reports 2FA OFF while a plaintext password is still stored
              // here — the operator removed it from their phone, or an earlier
              // removal's post-RPC clear failed. Every control that could drop that
              // stale copy lives in the `hasPassword` arm above, so without this row
              // the operator could neither SEE that a credential is still on disk for
              // this account nor get rid of it: the backend's own stale branch (clear
              // the column, spend no RPC) was unreachable from the UI.
              <div className="mb-4">
                <div className="border-b border-[#f0eeeb] py-[9px] text-[12.5px] font-medium text-danger">
                  {t('accounts.edit.twofaStoredStale')}
                </div>
                <div className="mt-[10px] text-center">
                  <button
                    type="button"
                    onClick={() => {
                      setConfirmRemove(true);
                    }}
                    className="bg-transparent p-0 text-[12.5px] font-medium text-danger"
                  >
                    {t('accounts.edit.twofaForget')}
                  </button>
                </div>
              </div>
            ) : null}
            <TwoFactorForm
              accountId={accountId}
              submitLabel={t('accounts.edit.twofaEnable')}
              onCreated={onCreated}
            />
          </>
        )}
      </Section>
      {confirmRemove ? (
        <ConfirmModal
          // Two situations, one modal and one DELETE: turning 2FA off on Telegram, or
          // dropping a stored password Telegram no longer has. The copy has to say
          // which — the second one costs the account nothing and warning about SMS
          // takeover there would be a lie. The value is the i18n key suffix, the
          // shape TwoFactorEmail's own two-branch modal already uses.
          title={t(`accounts.edit.twofa${removeKind}Title`)}
          body={t(`accounts.edit.twofa${removeKind}Body`)}
          confirmLabel={t(`accounts.edit.twofa${removeKind}Confirm`)}
          cancelLabel={t('accounts.edit.cancel')}
          onClose={() => {
            setConfirmRemove(false);
          }}
          onConfirm={onRemove}
        />
      ) : null}
    </>
  );
}
