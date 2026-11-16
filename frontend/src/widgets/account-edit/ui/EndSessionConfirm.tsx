import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { AccountRead } from '@/shared/api';
import { ConfirmModal, Input } from '@/shared/ui';

// «Выйти» and «Сбросить сессию» end the account's sign-ins in Telegram: the primary one AND
// the second device, and a reset also deletes their files. Getting the account back then
// takes a login code. So the dialog names every sign-in that ends, and for an account that
// is alive right now the operator types a word first: one stray click must not be able to
// lose a working account. A logout Telegram does not confirm changes nothing (the backend
// keeps the key, its file and the second device), and the card shows that answer.
export function EndSessionConfirm({
  account,
  kind,
  onClose,
  onConfirm,
}: {
  account: AccountRead;
  kind: 'logout' | 'reset';
  onClose: () => void;
  onConfirm: () => Promise<unknown>;
}) {
  const { t } = useTranslation();
  const [typed, setTyped] = useState('');
  const word = t('accounts.edit.endSession.word');
  const needsWord = account.status === 'alive';
  const matches = typed.trim().toLocaleUpperCase() === word.toLocaleUpperCase();
  const devices = account.devices?.length ? account.devices : ['desktop' as const];

  return (
    <ConfirmModal
      title={t(`accounts.edit.endSession.${kind}Title`)}
      body={
        <div className="flex flex-col gap-3">
          <p>{t(`accounts.edit.endSession.${kind}Body`)}</p>
          <ul className="list-disc pl-4">
            {devices.map((device, index) => (
              <li key={device}>
                {t(
                  index === 0
                    ? 'accounts.edit.endSession.primary'
                    : 'accounts.edit.endSession.second',
                  {
                    device: t(`accounts.edit.endSession.device.${device}`),
                  },
                )}
              </li>
            ))}
          </ul>
          <p>{t('accounts.edit.endSession.after')}</p>
          {needsWord ? (
            <label className="flex flex-col gap-1" htmlFor="end-session-word">
              <span className="text-content">
                {t('accounts.edit.endSession.typePrompt', { word })}
              </span>
              <Input
                id="end-session-word"
                autoComplete="off"
                value={typed}
                onChange={(event) => {
                  setTyped(event.target.value);
                }}
              />
            </label>
          ) : null}
        </div>
      }
      confirmLabel={t(`accounts.edit.endSession.${kind}Confirm`)}
      cancelLabel={t('accounts.edit.cancel')}
      confirmDisabled={needsWord && !matches}
      onClose={onClose}
      onConfirm={onConfirm}
    />
  );
}
