import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Badge, Button, FeedbackMark, Input } from '@/shared/ui';

import { addAccountDeviceMutation, DEVICE_TYPES, invalidateAccountViews } from '@/entities/account';
import type { AccountRead } from '@/shared/api';
import { mutationErrorText } from '@/shared/lib';

import { Section } from './_shared';
import { LABEL } from './_styles';

// Device fingerprint card: three immutable, locked fields (the profile is created
// at registration and never mutated — non-negotiable #9), the account's devices, and
// the operator's way to add the missing one as its own sign-in.
export function DeviceSection({ account }: { account: AccountRead }) {
  const { t } = useTranslation();
  return (
    <Section
      title={t('accounts.edit.device')}
      icon={
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className="text-content-subtle"
        >
          <rect x="3" y="11" width="18" height="11" rx="2" />
          <path d="M7 11V7a5 5 0 0 1 10 0v4" />
        </svg>
      }
    >
      <div className="mb-4 type-body text-content-subtle">{t('accounts.edit.deviceLocked')}</div>
      <div className="flex flex-col gap-3">
        <label>
          <span className={LABEL}>{t('accounts.edit.deviceModel')}</span>
          <Input
            tone="flat"
            value={account.device_model ?? '—'}
            disabled
            className="cursor-not-allowed text-content-subtle"
          />
        </label>
        <label>
          <span className={LABEL}>{t('accounts.edit.deviceOs')}</span>
          <Input
            tone="flat"
            value={account.device_system_version ?? '—'}
            disabled
            className="cursor-not-allowed text-content-subtle"
          />
        </label>
        <label>
          <span className={LABEL}>{t('accounts.edit.deviceLang')}</span>
          <Input
            tone="flat"
            value={account.device_lang ?? '—'}
            disabled
            className="cursor-not-allowed text-content-subtle"
          />
        </label>
      </div>
      <AccountDevices account={account} />
    </Section>
  );
}

// The devices the account signs in as, and — only on the operator's click — a sign-in
// of the missing one. The sign-in waits for Telegram's code to reach the existing
// session, so it can take a minute; a refusal is shown here with its reason.
function AccountDevices({ account }: { account: AccountRead }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [result, setResult] = useState<'ok' | 'err'>();
  const add = useMutation({ ...addAccountDeviceMutation(), meta: { inlineError: true } });
  const devices = account.devices ?? ['desktop'];
  const missing = DEVICE_TYPES.find((device) => !devices.includes(device));

  const onAdd = (device_type: (typeof DEVICE_TYPES)[number]) => {
    setResult(undefined);
    add.mutate(
      { path: { account_id: account.account_id }, body: { device_type } },
      {
        onSuccess: () => {
          setResult('ok');
          invalidateAccountViews(queryClient);
        },
        onError: () => {
          setResult('err');
        },
      },
    );
  };

  return (
    <div className="mt-4 flex flex-col gap-2">
      <span className={LABEL}>{t('accounts.devices.label')}</span>
      <div className="flex flex-wrap items-center gap-2">
        {devices.map((device) => (
          <Badge key={device}>{t(`accounts.devices.${device}`)}</Badge>
        ))}
        {missing ? (
          <span className="ml-auto flex items-center gap-2">
            <FeedbackMark result={add.isPending ? undefined : result} />
            <Button
              size="sm"
              loading={add.isPending}
              onClick={() => {
                onAdd(missing);
              }}
            >
              {t(`accounts.devices.add.${missing}`)}
            </Button>
          </span>
        ) : null}
      </div>
      {add.isPending ? (
        <div className="type-small text-content-subtle">{t('accounts.devices.adding')}</div>
      ) : add.isError ? (
        <div role="alert" className="type-small text-danger">
          {mutationErrorText(add.error)}
        </div>
      ) : null}
    </div>
  );
}
