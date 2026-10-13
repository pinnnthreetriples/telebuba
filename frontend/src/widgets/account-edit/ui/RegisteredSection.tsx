import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  estimateRegistrationYear,
  invalidateAccountViews,
  setAccountRegisteredAtMutation,
} from '@/entities/account';
import type { AccountRead } from '@/shared/api';
import { Button, Icon, Input } from '@/shared/ui';

import { Section } from './_shared';
import { LABEL } from './_styles';

// When the account was registered in Telegram (item 4.8). Only the operator's date
// counts: older than half a year opens DMs a day earlier, older than a year starts
// warming's «warming» phase on day 2. The year from the account id is shown as an «≈»
// hint only (decision Q15) — it is never saved.
export function RegisteredSection({ account }: { account: AccountRead }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const stored = account.telegram_registered_at ?? '';
  const [value, setValue] = useState(stored);
  const save = useMutation(setAccountRegisteredAtMutation());
  const estimate = estimateRegistrationYear(account.user_id);
  const submit = (next: string) => {
    save.mutate(
      { path: { account_id: account.account_id }, body: { registered_at: next || null } },
      {
        onSuccess: () => {
          invalidateAccountViews(queryClient);
        },
      },
    );
  };
  return (
    <Section
      title={t('accounts.edit.registered.title')}
      icon={<Icon name="calendar" size={12} className="text-content-subtle" />}
    >
      <label className="flex flex-col gap-1">
        <span className={LABEL}>{t('accounts.edit.registered.label')}</span>
        <Input
          type="date"
          value={value}
          max={new Date().toISOString().slice(0, 10)}
          onChange={(event) => {
            setValue(event.target.value);
          }}
        />
      </label>
      <p className="m-0 mt-2 type-small text-content-subtle">
        {estimate != null
          ? t('accounts.edit.registered.estimate', { year: estimate })
          : t('accounts.edit.registered.noEstimate')}
      </p>
      <div className="mt-3 flex gap-2">
        <Button
          variant="primary"
          size="sm"
          disabled={value === stored}
          loading={save.isPending}
          onClick={() => {
            submit(value);
          }}
        >
          {t('accounts.edit.registered.save')}
        </Button>
        {stored ? (
          <Button
            size="sm"
            onClick={() => {
              setValue('');
              submit('');
            }}
          >
            {t('accounts.edit.registered.clear')}
          </Button>
        ) : null}
      </div>
      {save.isError ? (
        <p className="m-0 mt-2 type-small text-danger-deep">
          {t('accounts.edit.registered.error')}
        </p>
      ) : null}
    </Section>
  );
}
