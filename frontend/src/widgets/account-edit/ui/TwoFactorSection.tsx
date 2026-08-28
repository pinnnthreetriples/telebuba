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
import { Button, ConfirmModal, Notice, Textarea } from '@/shared/ui';

import { TwoFactorEmail } from './TwoFactorEmail';
import { TwoFactorForm } from './TwoFactorForm';
import { Section, Spinner } from './_shared';

// One live fact row inside the 2FA-on state.
function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-md border-b border-line-row py-md">
      <span className="type-prose">{label}</span>
      <span className="text-right type-label text-content-primary">{value}</span>
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
  // The typed confirmation code, lifted for the same reason and by the same route: a
  // status refetch flips TwoFactorEmail's key and remounts it, which used to wipe
  // what the operator had just read out of the letter.
  const [emailCode, setEmailCode] = useState('');
  // 'failed' is a state of its own, not the absence of 'done': a rejected write has
  // to say so, because the manual selection below is then the only copy route left.
  const [copyState, setCopyState] = useState<'idle' | 'done' | 'failed'>('idle');
  const [changing, setChanging] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [open, setOpen] = useState(false);

  // Gated on the card being OPEN, and that gate is the point. GET /2fa is a live
  // `account.getPassword`: it borrows a pooled Telethon client and connects it to a
  // DC. Ungated, merely opening an account's detail view spent one Telegram round
  // trip per account — measured against a real dispatcher, a `.session` file appeared
  // and a connection was made from nothing but a page load — and it put a page load
  // in front of the pool's teardown path. The cost is that a closed card can no
  // longer show on/off at a glance; it shows "unknown" until the first read lands,
  // which is the honest label for a state nobody has asked Telegram about.
  //
  // `staleTime` so collapsing and reopening the card is a UI gesture rather than
  // another round trip. Writes invalidate the key explicitly, so it does not stand
  // between an operator and a change they just made.
  const twofa = useQuery({
    ...accountTwofaQueryOptions({ path: { account_id: accountId } }),
    enabled: open,
    staleTime: 5 * 60 * 1000,
  });
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
  const readFailed = readError != null || twofa.isError;
  // One modal and one DELETE serve three situations, and the copy has to say which.
  // The third is the read-failure one: `remove_account_twofa` only takes its "clear
  // the column, spend no RPC" branch when the live read SAYS 2FA is off, so with no
  // live status the DELETE does attempt a real removal on Telegram — the plain
  // "Telegram keeps its copy" wording of the stale case would be a lie here.
  const removeKind = readFailed ? 'ForgetUnknown' : hasPassword ? 'Disable' : 'Forget';
  // `previous_kept` is three-valued and only two of them mean "nothing was kept".
  // `true`: kept, and the live read says Telegram holds a password. `null`: kept, but
  // the live read answered nothing. Both are deliberate, so neither is a store
  // failure; `false`/absent is a fresh set, or a live read saying Telegram has no
  // password at all, which makes any stored value stale by definition.
  const keptPrevious = created?.previous_kept === true || created?.previous_kept === null;

  const onCreated = (result: AccountTwoFactorCreated) => {
    setCreated(result);
    setChanging(false);
    invalidate();
  };

  // The clipboard precedent for this codebase, and it lives or dies on the FAILURE
  // states: this panel is the only copy of the password, so "Скопировано" over a
  // write that rejected (denied permission, an unfocused document — Chrome rejects
  // for that) loses the credential the moment the operator clicks Готово. Hence a
  // label driven by how the promise SETTLED, never by having called it.
  //
  // `navigator.clipboard` is absent altogether in any non-secure context — the
  // dashboard reached over http:// by LAN IP instead of localhost, and happy-dom —
  // where a copy button would be dead. There the panel drops the button and says to
  // select the password and copy it by hand instead.
  const clipboard: Clipboard | undefined = navigator.clipboard;
  const copyPassword = (password: string) => {
    if (!clipboard) return;
    void clipboard.writeText(password).then(
      () => {
        setCopyState('done');
        later(() => {
          // Only clears the state it was scheduled for. A flat `setCopyState('idle')`
          // let a successful copy's timer land on a LATER rejected one and silently
          // erase the warning — the only signal that the operator's single copy of
          // the credential did not make it to the clipboard.
          setCopyState((state) => (state === 'done' ? 'idle' : state));
        }, 2400);
      },
      () => {
        // No auto-reset: this one has to stay until the operator has copied it by
        // hand, which is the only route left.
        setCopyState('failed');
      },
    );
  };

  const onRemove = () =>
    removeTwofa
      .mutateAsync({
        path: { account_id: accountId },
        // `forget_only` only in the read-failure branch. There the DELETE would
        // otherwise spend a real `updatePasswordSettings` against a live state we
        // could not read — a write on a guess, to clear one of our own columns. It is
        // also the only exit from "the column holds a password Telegram does not
        // accept", which is exactly what a failed read cannot rule out. The other two
        // branches keep the plain DELETE: with a live status the backend decides
        // between removing on Telegram and its own stale clear, and that decision
        // needs the RPC path left open.
        ...(removeKind === 'ForgetUnknown' ? { query: { forget_only: true } } : {}),
      })
      .then(() => {
        setChanging(false);
        invalidate();
      });

  return (
    <>
      <Section
        title={t('accounts.edit.twofa')}
        onOpenChange={(next) => {
          setOpen(next);
          // The plaintext is the operator's only copy, not a value to leave
          // sitting in a card they walked away from.
          if (!next) {
            setCreated(null);
            setCopyState('idle');
            setChanging(false);
            setEmailCodeLength(null);
            setEmailCode('');
          }
        }}
        right={
          twofa.isFetching ? (
            <Spinner size={13} />
          ) : (
            <span
              className={`shrink-0 rounded-full px-md py-xs text-tiny font-medium ${
                hasPassword ? 'bg-success-tint text-success-deep' : 'bg-canvas text-content-muted'
              }`}
            >
              {/* No data and nothing in flight means nobody has asked Telegram yet
                  (see the query gate above) — a spinner would claim otherwise. */}
              {!twofa.data
                ? t('accounts.edit.twofaUnknown')
                : hasPassword
                  ? t('accounts.edit.twofaOn')
                  : t('accounts.edit.twofaOff')}
            </span>
          )
        }
      >
        {/* Somebody is trying to take the account with a password reset. Above the
            state branches on purpose: a status can carry a pending reset with
            `has_password: false` (Telegram has already dropped the password the
            reset was requested against), and inside the 2FA-on arm that warning was
            silently dropped in exactly the case where it matters most. */}
        {status?.pending_reset_date ? (
          <div className="border-b border-line-row py-md text-body font-semibold text-danger">
            {t('accounts.edit.twofaResetRequested', {
              date: status.pending_reset_date.slice(0, 10),
            })}
          </div>
        ) : null}
        {twofa.isPending ? (
          <div className="py-sm">
            <Spinner size={16} />
          </div>
        ) : created ? (
          <>
            <div className="mb-md type-item-title">{t('accounts.edit.twofaCreatedTitle')}</div>
            {created.stored === false && !keptPrevious ? (
              // The RPC landed but the DB write did not, so this response is the
              // ONLY copy and change/removal are gone until it is set again. NOT the
              // unconfirmed-change cases: nothing failed there, the previous password
              // was kept on purpose (`true` or `null`) and the warning below says so.
              <Notice tone="danger" className="mb-md py-md text-tiny font-medium">
                {t('accounts.edit.twofaStoreFailed')}
              </Notice>
            ) : null}
            {created.confirmed === false ? (
              // The request was on the wire and only the answer was lost, so
              // Telegram may or may not hold this password. Either way it is the
              // only copy — as loud as the store-failure warning above. On a CHANGE
              // the backend deliberately kept the OLD password stored rather than
              // overwrite a credential known to work, so the operator has two
              // candidates and has to check which one the phone asks for — unless
              // the read that would have proved Telegram holds ANY password answered
              // nothing either (`previous_kept: null`), and then not even "one of
              // these two is in force" is sayable.
              <Notice tone="danger" className="mb-md py-md text-tiny font-medium">
                {created.previous_kept === true
                  ? t('accounts.edit.twofaUnconfirmedChange')
                  : created.previous_kept === null
                    ? t('accounts.edit.twofaUnconfirmedKept')
                    : t('accounts.edit.twofaUnconfirmed')}
              </Notice>
            ) : null}
            {/* A textarea, and on its own row. Measured in Chrome at a 355px viewport,
                the input this replaces reported scrollWidth 196 against clientWidth
                160 with `overflow: clip` and no ellipsis: the value was cut with
                nothing on screen saying so, while the panel's own fallback asks the
                operator to select and copy it by hand. An input cannot wrap; a
                readonly textarea wraps (and scrolls past two rows for an unusually
                long typed password), stays selectable, and keeps the label. The copy
                button moves below so the field gets the full card width. */}
            <Textarea
              tone="flat"
              readOnly
              rows={2}
              value={created.password}
              aria-label={t('accounts.edit.twofaNewPassword')}
              className="mb-sm resize-none break-all font-mono text-content-primary"
            />
            {clipboard ? (
              <button
                type="button"
                onClick={() => {
                  copyPassword(created.password);
                }}
                className="mb-md w-full rounded-lg border border-line bg-surface-card px-md py-md text-body font-medium text-content-muted"
              >
                {copyState === 'done'
                  ? t('accounts.edit.twofaCopied')
                  : t('accounts.edit.twofaCopy')}
              </button>
            ) : null}
            {clipboard ? null : (
              <div className="mb-md type-caption">{t('accounts.edit.twofaCopyManual')}</div>
            )}
            {copyState === 'failed' ? (
              <div className="mb-md type-caption font-medium text-danger">
                {t('accounts.edit.twofaCopyFailed')}
              </div>
            ) : null}
            <Button
              size="block"
              onClick={() => {
                setCreated(null);
                setCopyState('idle');
              }}
            >
              {t('accounts.edit.twofaDone')}
            </Button>
          </>
        ) : readFailed ? (
          <>
            {/* A set, change or disable against an account whose live state we could
                not read is a guess, so this branch offers none of them. */}
            <div className="type-caption text-danger">
              {t('accounts.edit.twofaReadErr', {
                reason: readError
                  ? t(`shell.code.${readError}`, { defaultValue: readError })
                  : t('shell.mutationError'),
              })}
            </div>
            {hasStored ? (
              // The one exception, and it is not a guess: `has_stored_password` is a
              // DB fact the backend answers even when the live read failed, and
              // dropping our copy needs no successful read. Without this row a
              // transient `twofa_state_unreadable` left a plaintext cloud password
              // sitting on disk that the card neither showed nor could clear —
              // every control that can do it lived in the `hasPassword` arm.
              <div className="mt-md">
                <div className="border-b border-line-row py-md text-body font-medium text-content-muted">
                  {t('accounts.edit.twofaStored')}
                </div>
                <div className="mt-md text-center">
                  <button
                    type="button"
                    onClick={() => {
                      setConfirmRemove(true);
                    }}
                    className="bg-transparent p-0 text-body font-medium text-danger"
                  >
                    {t('accounts.edit.twofaForget')}
                  </button>
                </div>
              </div>
            ) : null}
            {status?.email_unconfirmed_pattern ? (
              // The other thing a failed read does not invalidate: confirming a
              // pending address, having the code mailed again, and cancelling it are
              // authorised by neither the live state nor a stored password, so a
              // verification already under way stays finishable. Without this the
              // address was simply unreachable — the whole email leg lived in the
              // `hasPassword` arm.
              //
              // Reachable when a read that HAD succeeded starts failing: react-query
              // keeps the last good data beside the error. The envelope branch
              // (`error` set) carries no status at all — `_live_status` returns one or
              // the other, never both — so nothing is invented here, the pattern is
              // simply absent and this renders nothing.
              <TwoFactorEmail
                key={status.email_unconfirmed_pattern}
                accountId={accountId}
                // Whether a CONFIRMED address exists is exactly what could not be
                // read, so neither claim may be printed: `null` renders no row.
                hasRecovery={null}
                // Attaching and detaching are the two email writes that need the
                // stored password, and this branch offers neither.
                hasStored={false}
                unconfirmedPattern={status.email_unconfirmed_pattern}
                code={emailCode}
                onCode={setEmailCode}
                codeLength={emailCodeLength}
                onCodeLength={setEmailCodeLength}
                onChanged={invalidate}
              />
            ) : null}
          </>
        ) : hasPassword ? (
          <>
            <Fact
              label={t('accounts.edit.twofaHint')}
              // `||`, not `??`: '' is not nullish, and '' is exactly what the
              // gateway reports once this card has cleared a hint.
              value={status?.hint || t('accounts.edit.twofaHintNone')}
            />
            {/* No recovery-email summary row here. TwoFactorEmail below states that
                same fact in every state it can honestly claim it, and the two rows
                landing one under the other read as a duplicate — the runtime auditor
                saw "Резервная почта / не привязана" twice. Neither state lost its
                reachability: the row moved, it did not go. */}
            <div
              className={`border-b border-line-row py-md text-body font-medium ${
                hasStored ? 'text-content-muted' : 'text-danger'
              }`}
            >
              {hasStored ? t('accounts.edit.twofaStored') : t('accounts.edit.twofaNotStored')}
            </div>
            {hasStored ? null : (
              <div className="mt-md type-caption">{t('accounts.edit.twofaNotStoredNote')}</div>
            )}
            <TwoFactorEmail
              // Keyed on the server-side email state: a write's optimistic
              // override then lives exactly until the status confirms it.
              key={`${String(status?.has_recovery === true)}|${status?.email_unconfirmed_pattern ?? ''}`}
              accountId={accountId}
              hasRecovery={status?.has_recovery === true}
              // Rendered whatever `has_stored_password` says, because three of the
              // five email operations need no stored password: confirm, resend and
              // cancel go straight through on the backend. Only attach and detach are
              // gated, inside — and hiding the block instead left a pending address
              // invisible in a state that is reachable whenever the password was set
              // from the phone, kept by a `previous_kept` change, or failed to store.
              hasStored={hasStored}
              unconfirmedPattern={status?.email_unconfirmed_pattern ?? null}
              code={emailCode}
              onCode={setEmailCode}
              codeLength={emailCodeLength}
              onCodeLength={setEmailCodeLength}
              onChanged={invalidate}
            />
            <div className="mt-lg">
              {changing ? (
                <TwoFactorForm
                  accountId={accountId}
                  submitLabel={t('accounts.edit.twofaChange')}
                  initialHint={status?.hint ?? ''}
                  onCreated={onCreated}
                />
              ) : (
                <Button
                  size="block"
                  onClick={() => {
                    setChanging(true);
                  }}
                  disabled={!hasStored}
                >
                  {t('accounts.edit.twofaChange')}
                </Button>
              )}
            </div>
            <div className="mt-md text-center">
              <button
                type="button"
                onClick={() => {
                  setConfirmRemove(true);
                }}
                disabled={!hasStored}
                className="bg-transparent p-0 text-body font-medium text-danger disabled:opacity-50"
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
              <div className="mb-lg">
                <div className="border-b border-line-row py-md text-body font-medium text-danger">
                  {t('accounts.edit.twofaStoredStale')}
                </div>
                <div className="mt-md text-center">
                  <button
                    type="button"
                    onClick={() => {
                      setConfirmRemove(true);
                    }}
                    className="bg-transparent p-0 text-body font-medium text-danger"
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
          // Three situations, one modal and one DELETE: turning 2FA off on Telegram,
          // dropping a stored password Telegram no longer has, and dropping one while
          // the live state could not be read at all. The copy has to say which — the
          // second costs the account nothing and warning about SMS takeover there
          // would be a lie, while the third cannot promise either way. The value is
          // the i18n key suffix, the shape TwoFactorEmail's own modal already uses.
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
