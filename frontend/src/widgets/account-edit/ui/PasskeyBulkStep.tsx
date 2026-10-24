import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountDisplayName,
  allAccountsQueryOptions,
  createAccountPasskeyMutation,
} from '@/entities/account';
import { errorMessage } from '@/shared/lib';
import { Button, Notice } from '@/shared/ui';

import { BulkAccountChecklist } from './BulkAccountChecklist';
import { BulkProgress } from './BulkProgress';
import { useBulkRun } from './useBulkRun';

// A random 15–30 s gap between accounts. Telegram's limits are per account, so the
// gap is not about flood waits: it keeps a batch from registering a passkey on
// every fresh account in the same second, which is what automation looks like.
const PAUSE_MIN_MS = 15_000;
const PAUSE_MAX_MS = 30_000;
const pause = () => PAUSE_MIN_MS + Math.random() * (PAUSE_MAX_MS - PAUSE_MIN_MS);
// Rough per-account time for the estimate: the vault key plus three Telegram calls.
const STEP_ESTIMATE_MS = 5_000;

function estimateMinutes(count: number): number {
  const ms = (count - 1) * ((PAUSE_MIN_MS + PAUSE_MAX_MS) / 2) + count * STEP_ESTIMATE_MS;
  return Math.max(1, Math.round(ms / 60_000));
}

// The add wizard's passkey step: give the accounts just added a Telegram passkey
// (the private key is created in the vault), one account at a time with a pause.
// Shown only when the vault is configured — the wizard asks before it adds the step.
//
// The batch runs in this window, like the cloud-password step: closing it stops the
// batch, and the passkeys already created stay. An account that already has one
// counts as done. A vault failure stops the batch, since every next account would
// fail the same way.
export function PasskeyBulkStep({
  accountIds,
  sources,
  onDone,
}: {
  accountIds: string[];
  sources: Record<string, string>;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const [phase, setPhase] = useState<'select' | 'running' | 'result'>('select');
  const [selected, setSelected] = useState<string[]>(accountIds);
  const [existing, setExisting] = useState<string[]>([]);
  const [vaultDown, setVaultDown] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const accounts = useQuery(allAccountsQueryOptions());
  // Inline: each row shows its own reason, a toast per account would pile up.
  const create = useMutation({ ...createAccountPasskeyMutation(), meta: { inlineError: true } });
  const bulk = useBulkRun();

  // The countdown's clock, ticking only while the batch waits between accounts.
  useEffect(() => {
    if (bulk.nextAt === null) return;
    setNow(Date.now());
    const id = window.setInterval(() => {
      setNow(Date.now());
    }, 1_000);
    return () => {
      window.clearInterval(id);
    };
  }, [bulk.nextAt]);

  const label = (accountId: string) => {
    const account = accounts.data?.items.find((item) => item.account_id === accountId);
    const name = account ? accountDisplayName(account).trim() : '';
    return name && name !== accountId ? name : accountId;
  };

  const step = async (accountId: string) => {
    try {
      await create.mutateAsync({ path: { account_id: accountId } });
    } catch (error) {
      const code = errorMessage(error) ?? '';
      if (code === 'passkey_exists') {
        setExisting((prev) => [...prev, accountId]);
        return;
      }
      if (code.startsWith('passkey_vault_')) {
        setVaultDown(true);
        bulk.stop();
      }
      throw error;
    }
  };

  const start = (ids: string[]) => {
    setVaultDown(false);
    setPhase('running');
    void bulk.run(ids, step, pause).then(() => {
      setPhase('result');
    });
  };

  if (phase === 'select') {
    return (
      <>
        <div className="mb-4 type-body text-content-subtle">
          {t('accounts.addWizard.passkeyIntro')}
        </div>
        <BulkAccountChecklist
          accountIds={accountIds}
          sources={sources}
          selected={selected}
          onChange={setSelected}
          label={label}
        />
        <div className="mt-3 type-small">
          {selected.length > 1
            ? t('accounts.addWizard.passkeyPacing', { minutes: estimateMinutes(selected.length) })
            : t('accounts.addWizard.passkeyPacingOne')}
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <Button onClick={onDone}>{t('accounts.addWizard.skip')}</Button>
          <Button
            variant="primary"
            disabled={selected.length === 0}
            onClick={() => {
              start(selected);
            }}
          >
            {t('accounts.addWizard.passkeyCreateCount', { count: selected.length })}
          </Button>
        </div>
      </>
    );
  }

  const okLabel = (accountId: string) =>
    existing.includes(accountId)
      ? t('accounts.addWizard.passkeyRowExisting')
      : t('accounts.addWizard.passkeyRowOk');
  const progress = <BulkProgress rows={bulk.rows} label={label} okLabel={okLabel} />;

  if (phase === 'running') {
    const seconds = bulk.nextAt === null ? 0 : Math.max(0, Math.ceil((bulk.nextAt - now) / 1000));
    return (
      <>
        {progress}
        <div className="mt-3 type-small" aria-live="polite">
          {bulk.nextAt === null
            ? t('accounts.addWizard.passkeyRunningNote')
            : t('accounts.addWizard.passkeyNextIn', { s: seconds })}
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <Button onClick={bulk.stop}>{t('accounts.addWizard.twofaStop')}</Button>
          <Button variant="primary" loading disabled>
            {t('accounts.addWizard.passkeyWorking')}
          </Button>
        </div>
      </>
    );
  }

  const ok = bulk.rows.filter((row) => row.state === 'ok').length;
  const retry = bulk.rows
    .filter((row) => row.state === 'error' || row.state === 'skipped')
    .map((row) => row.accountId);
  return (
    <>
      <div className="mb-4">
        {vaultDown ? (
          <Notice tone="danger">{t('accounts.addWizard.passkeyVaultDown')}</Notice>
        ) : (
          <Notice tone={retry.length > 0 ? 'warning' : 'success'}>
            {t('accounts.addWizard.passkeyResult', { done: ok, total: bulk.rows.length })}
          </Notice>
        )}
      </div>
      {progress}
      <div className="mt-6 flex justify-end gap-2">
        {retry.length > 0 ? (
          <Button
            onClick={() => {
              start(retry);
            }}
          >
            {t('accounts.addWizard.passkeyRetry', { count: retry.length })}
          </Button>
        ) : null}
        <Button variant="primary" onClick={onDone}>
          {t('accounts.addWizard.done')}
        </Button>
      </div>
    </>
  );
}
