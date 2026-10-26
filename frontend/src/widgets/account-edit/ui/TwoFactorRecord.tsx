import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { recordAccountTwofaMutation } from '@/entities/account';
import { mutationErrorText } from '@/shared/lib';
import { Button, Input } from '@/shared/ui';

// The account already HAS a cloud password on Telegram (set on the phone, or bought
// with the session) and this dashboard holds no copy. Typing it here asks Telegram to
// verify it (nothing changes there) and only then stores it, so web and emulator
// sign-in can enter it by themselves. A wrong password is refused and nothing is kept.
export function TwoFactorRecord({
  accountId,
  onRecorded,
}: {
  accountId: string;
  onRecorded: () => void;
}) {
  const { t } = useTranslation();
  const [password, setPassword] = useState('');
  // `gcTime: 0` + `reset()`: the typed password sits in the mutation's `variables`,
  // and reset() only schedules collection, so the 5-minute default would keep it.
  const record = useMutation({
    ...recordAccountTwofaMutation(),
    gcTime: 0,
    meta: { inlineError: true },
  });

  const submit = () => {
    const value = password.trim();
    if (!value) return;
    record.mutate(
      { path: { account_id: accountId }, body: { password: value } },
      {
        onSuccess: () => {
          setPassword('');
          record.reset();
          onRecorded();
        },
      },
    );
  };

  return (
    <div className="mt-4 flex flex-col gap-2">
      <label htmlFor={`twofa-record-${accountId}`} className="type-small-medium">
        {t('accounts.edit.twofaRecordLabel')}
      </label>
      <Input
        id={`twofa-record-${accountId}`}
        type="password"
        className="font-mono"
        value={password}
        // The ACCOUNT's password, never the operator's: keep the browser's saved
        // credential out, as the set form does.
        autoComplete="new-password"
        onChange={(event) => {
          setPassword(event.target.value);
        }}
      />
      {record.isError ? (
        <div className="type-small text-danger-deep">{mutationErrorText(record.error)}</div>
      ) : null}
      <Button
        fullWidth
        className="font-medium"
        loading={record.isPending}
        disabled={!password.trim()}
        onClick={submit}
      >
        {t('accounts.edit.twofaRecordSubmit')}
      </Button>
      <div className="type-small">{t('accounts.edit.twofaRecordNote')}</div>
    </div>
  );
}
