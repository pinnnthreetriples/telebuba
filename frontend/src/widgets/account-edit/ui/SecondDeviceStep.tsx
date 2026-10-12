import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { addAccountDeviceMutation, type DeviceType } from '@/entities/account';
import { mutationErrorText } from '@/shared/lib';
import { Button, FeedbackMark, Icon, IconButton, Spinner } from '@/shared/ui';

type Row = { state: 'pending' | 'working' | 'ok' | 'error'; error?: string };

// The wizard step for "both": sign the other device in for every account just added,
// as its own session. It runs AFTER the proxy step, so the new sign-in goes through the
// account's proxy, and only when the operator presses the button — each sign-in makes
// Telegram send the live account a login code. One account at a time: each waits for
// its code to reach the existing session. A refusal is the account's own row, with
// its reason and a retry; skipping keeps every account on the one device it has.
export function SecondDeviceStep({
  accountIds,
  sources,
  device,
  onDone,
  onImported,
}: {
  accountIds: string[];
  // accountId → the file name or number the operator recognises it by.
  sources: Record<string, string>;
  device: DeviceType;
  onDone: () => void;
  onImported: () => void;
}) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<Record<string, Row>>({});
  const add = useMutation({ ...addAccountDeviceMutation(), meta: { inlineError: true } });
  const running = Object.values(rows).some((row) => row.state === 'working');
  const started = Object.keys(rows).length > 0;

  const setRow = (accountId: string, row: Row) => {
    setRows((prev) => ({ ...prev, [accountId]: row }));
  };

  // mutateAsync in a loop: one useMutation is ONE callback slot, so `.mutate` per
  // account would drop every result but the last.
  const run = async (ids: string[]) => {
    for (const accountId of ids) {
      setRow(accountId, { state: 'working' });
      try {
        await add.mutateAsync({
          path: { account_id: accountId },
          body: { device_type: device },
        });
        setRow(accountId, { state: 'ok' });
      } catch (error) {
        setRow(accountId, { state: 'error', error: mutationErrorText(error) });
      } finally {
        onImported();
      }
    }
  };

  return (
    <>
      <div className="mb-4 type-body text-content-subtle">
        {t('accounts.addWizard.deviceStepHint', {
          device: t(`accounts.devices.${device}`),
        })}
      </div>
      <div className="flex flex-col gap-3">
        {accountIds.map((accountId) => {
          const row = rows[accountId] ?? { state: 'pending' };
          return (
            <div
              key={accountId}
              className="flex items-center gap-3 rounded-md border border-line bg-surface-card px-3 py-3"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate type-body-medium">{sources[accountId] ?? accountId}</div>
                <div
                  className={`text-small ${row.state === 'error' ? 'text-danger' : 'text-content-subtle'}`}
                >
                  {row.state === 'error'
                    ? row.error
                    : t(`accounts.addWizard.deviceState.${row.state}`)}
                </div>
              </div>
              {row.state === 'working' ? <Spinner className="m-1" /> : null}
              {row.state === 'ok' ? <FeedbackMark result="ok" /> : null}
              {row.state === 'error' ? (
                <IconButton
                  tone="primary"
                  aria-label={t('accounts.addWizard.retry')}
                  title={t('accounts.addWizard.retry')}
                  disabled={running}
                  onClick={() => {
                    void run([accountId]);
                  }}
                >
                  <Icon name="refresh" size={16} />
                </IconButton>
              ) : null}
            </div>
          );
        })}
      </div>
      <div className="mt-6 flex justify-end gap-2">
        {started ? null : (
          <Button className="text-content-muted" onClick={onDone}>
            {t('accounts.addWizard.skip')}
          </Button>
        )}
        {started ? (
          <Button variant="primary" disabled={running} onClick={onDone}>
            {t('accounts.addWizard.next')}
          </Button>
        ) : (
          <Button
            variant="primary"
            onClick={() => {
              void run(accountIds);
            }}
          >
            {t('accounts.addWizard.deviceStart', { device: t(`accounts.devices.${device}`) })}
          </Button>
        )}
      </div>
    </>
  );
}
