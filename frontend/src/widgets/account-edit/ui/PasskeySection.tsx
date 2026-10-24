import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountPasskeyQueryKey,
  accountPasskeyQueryOptions,
  createAccountPasskeyMutation,
  invalidateAccountViews,
  loginAccountWithPasskeyMutation,
  removeAccountPasskeyMutation,
} from '@/entities/account';
import type { AccountRead } from '@/shared/api';
import { errorMessage, formatLocalDateTime } from '@/shared/lib';
import { Button, ConfirmModal, Notice, Spinner } from '@/shared/ui';

import { Section } from './_shared';

// Telegram passkey card. The private key lives in the key vault, so nothing here
// is secret: the card only says whether a passkey exists and offers to create one,
// sign the session back in with it (no SMS code), or remove it. A refusal arrives
// as a stable code and is toasted by the shared mutation cache.
//
// Removing from Telegram needs a live session, which a dead account lacks; when
// Telegram could not be reached or refused, «Забыть ключ» removes it from Telebuba
// and the vault only. Without the vault key the passkey Telegram lists is inert.
const FORGETTABLE = new Set(['unavailable', 'passkey_telegram_refused']);

export function PasskeySection({ account }: { account: AccountRead }) {
  const { t, i18n } = useTranslation();
  const accountId = account.account_id;
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [confirmForget, setConfirmForget] = useState(false);

  // A DB read only, but gated like the other cards so a closed card costs nothing.
  const passkey = useQuery({
    ...accountPasskeyQueryOptions({ path: { account_id: accountId } }),
    enabled: open,
  });
  const create = useMutation(createAccountPasskeyMutation());
  const login = useMutation(loginAccountWithPasskeyMutation());
  const remove = useMutation(removeAccountPasskeyMutation());
  const busy = create.isPending || login.isPending || remove.isPending;

  const refresh = () => {
    void queryClient.invalidateQueries({
      queryKey: accountPasskeyQueryKey({ path: { account_id: accountId } }),
    });
    invalidateAccountViews(queryClient);
  };
  const path = { path: { account_id: accountId } };
  const view = passkey.data;
  const hasPasskey = view?.has_passkey === true;
  const forgettable = FORGETTABLE.has(errorMessage(remove.error) ?? '');

  return (
    <>
      <Section
        title={t('accounts.edit.passkey')}
        onOpenChange={setOpen}
        right={
          passkey.isFetching || busy ? (
            <Spinner />
          ) : (
            <span
              className={`shrink-0 rounded-full px-3 py-1 text-small font-medium ${
                hasPasskey ? 'bg-success-tint text-success-deep' : 'bg-canvas text-content-muted'
              }`}
            >
              {!view
                ? t('accounts.edit.passkeyUnknown')
                : hasPasskey
                  ? t('accounts.edit.passkeyOn')
                  : t('accounts.edit.passkeyOff')}
            </span>
          )
        }
      >
        {view && !view.vault_configured ? (
          <div className="mb-3">
            <Notice tone="warning" className="py-3 text-small font-medium">
              {t('accounts.edit.passkeyVaultMissing')}
            </Notice>
          </div>
        ) : null}
        <p className="mb-3 type-body text-content-subtle">{t('accounts.edit.passkeyHint')}</p>
        {hasPasskey && view?.created_at ? (
          <div className="mb-3 type-body text-content-subtle">
            {t('accounts.edit.passkeyCreatedAt', {
              date: formatLocalDateTime(Date.parse(view.created_at), i18n.language),
            })}
          </div>
        ) : null}
        {view?.vault_configured ? (
          <div className="flex flex-wrap gap-2">
            {hasPasskey ? (
              <>
                <Button
                  disabled={busy}
                  onClick={() => {
                    login.mutate(path, { onSettled: refresh });
                  }}
                >
                  {t('accounts.edit.passkeyLogin')}
                </Button>
                <Button
                  variant="secondary"
                  disabled={busy}
                  onClick={() => {
                    setConfirmRemove(true);
                  }}
                >
                  {t('accounts.edit.passkeyRemove')}
                </Button>
                {forgettable ? (
                  <Button
                    variant="secondary"
                    disabled={busy}
                    onClick={() => {
                      setConfirmForget(true);
                    }}
                  >
                    {t('accounts.edit.passkeyForget')}
                  </Button>
                ) : null}
              </>
            ) : (
              <Button
                disabled={busy || !view}
                onClick={() => {
                  create.mutate(path, { onSettled: refresh });
                }}
              >
                {t('accounts.edit.passkeyCreate')}
              </Button>
            )}
          </div>
        ) : null}
      </Section>
      {confirmRemove ? (
        <ConfirmModal
          title={t('accounts.edit.passkeyRemoveTitle')}
          body={t('accounts.edit.passkeyRemoveBody')}
          confirmLabel={t('accounts.edit.passkeyRemove')}
          cancelLabel={t('accounts.edit.cancel')}
          onClose={() => {
            setConfirmRemove(false);
          }}
          onConfirm={() => remove.mutateAsync(path).finally(refresh)}
        />
      ) : null}
      {confirmForget ? (
        <ConfirmModal
          title={t('accounts.edit.passkeyForgetTitle')}
          body={t('accounts.edit.passkeyForgetBody')}
          confirmLabel={t('accounts.edit.passkeyForget')}
          cancelLabel={t('accounts.edit.cancel')}
          onClose={() => {
            setConfirmForget(false);
          }}
          onConfirm={() =>
            remove.mutateAsync({ ...path, query: { forget_only: true } }).finally(refresh)
          }
        />
      ) : null}
    </>
  );
}
