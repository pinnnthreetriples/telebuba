import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountSessionAuditQueryOptions,
  allAccountsQueryOptions,
  auditAccountSessionsMutation,
  checkAccountMutation,
  closeAccountForeignSessionsMutation,
} from '@/entities/account';
import { mutationErrorText } from '@/shared/lib';
import { Button, FeedbackMark, Icon, IconButton, Notice, Spinner } from '@/shared/ui';

import { isRefused, sessionOutcomeText } from './_sessionOutcome';
import { useMountedRef } from './useMountedRef';

type RowState =
  | 'pending'
  | 'working'
  // No foreign session: the account goes on.
  | 'clean'
  // Foreign sessions were found and closed (by the option or by the operator).
  | 'closed'
  // Foreign sessions are there and nobody decided yet: the account is held out of the
  // cloud-password and passkey steps.
  | 'foreign'
  // The check or the audit did not answer; held the same way, with a retry.
  | 'unchecked'
  // The operator chose to go on with foreign sessions standing.
  | 'continued'
  // No proxy: nothing here may talk to Telegram. The later steps refuse it on their own.
  | 'noProxy';

type Row = { state: RowState; foreign?: number; reason?: string };

// The states whose account goes on to the cloud password and the passkey.
const PROCEEDS: ReadonlySet<RowState> = new Set(['clean', 'closed', 'continued', 'noProxy']);

// The wizard step between the proxy and the cloud password: first CHECK each account (its
// session is alive behind its proxy), then AUDIT who else holds it, and only then may the
// cloud password and the passkey be set. An account with foreign sessions stops here, at a
// visible decision, while the rest of the batch goes on: close them, or go on regardless.
// An unanswered account is held the same way. With the import option on, foreign sessions
// are closed as soon as they are found.
export function SessionAuditStep({
  accountIds,
  sources,
  autoClose,
  onDone,
  onImported,
}: {
  accountIds: string[];
  // accountId → the file name or number the operator recognises it by.
  sources: Record<string, string>;
  autoClose: boolean;
  // The accounts that go on to the next steps.
  onDone: (proceeding: string[]) => void;
  onImported: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const alive = useMountedRef();
  const started = useRef(false);
  const [rows, setRows] = useState<Record<string, Row>>({});
  const check = useMutation({ ...checkAccountMutation(), meta: { inlineError: true } });
  const audit = useMutation({ ...auditAccountSessionsMutation(), meta: { inlineError: true } });
  const closeForeign = useMutation({
    ...closeAccountForeignSessionsMutation(),
    meta: { inlineError: true },
  });

  const setRow = (accountId: string, row: Row) => {
    if (alive.current) setRows((prev) => ({ ...prev, [accountId]: row }));
  };

  // Close what is foreign now; the row ends `closed`, or stays `foreign` with why.
  const closeOne = async (accountId: string): Promise<void> => {
    setRow(accountId, { state: 'working' });
    try {
      const result = await closeForeign.mutateAsync({ path: { account_id: accountId } });
      const refused = result.outcomes.find(isRefused);
      const left = result.audit?.foreign_count ?? (refused ? 1 : 0);
      setRow(
        accountId,
        left === 0
          ? { state: 'closed' }
          : {
              state: 'foreign',
              foreign: left,
              reason: refused ? sessionOutcomeText(t, refused) : undefined,
            },
      );
    } catch (error) {
      setRow(accountId, { state: 'foreign', foreign: 1, reason: mutationErrorText(error) });
    }
  };

  // CHECK, then AUDIT, for one account.
  const examine = async (accountId: string, proxied: boolean): Promise<void> => {
    if (!proxied) {
      setRow(accountId, { state: 'noProxy' });
      return;
    }
    setRow(accountId, { state: 'working' });
    try {
      const checked = await check.mutateAsync({ body: { account_id: accountId } });
      if (checked.status !== 'alive') {
        setRow(accountId, {
          state: 'unchecked',
          reason: t(`accounts.status.${checked.status}`),
        });
        return;
      }
      // The check already audited an account seen for the first time; read what it saved
      // and audit only when there is nothing yet (which also reports why it could not).
      const saved = await queryClient.fetchQuery({
        ...accountSessionAuditQueryOptions({ path: { account_id: accountId } }),
        staleTime: 0,
      });
      const result = saved.checked_at
        ? saved
        : await audit.mutateAsync({ path: { account_id: accountId } });
      if (result.foreign_count === 0) {
        setRow(accountId, { state: 'clean' });
      } else if (autoClose) {
        await closeOne(accountId);
      } else {
        setRow(accountId, { state: 'foreign', foreign: result.foreign_count });
      }
    } catch (error) {
      setRow(accountId, { state: 'unchecked', reason: mutationErrorText(error) });
    } finally {
      onImported();
    }
  };

  // One account at a time: every check and audit is a Telegram connection, and a batch
  // reading its whole fleet at once is what the proxy pool is there to avoid.
  const run = async (ids: string[]) => {
    const fleet = await queryClient.fetchQuery({ ...allAccountsQueryOptions(), staleTime: 0 });
    const proxied = new Map(fleet.items.map((row) => [row.account_id, Boolean(row.proxy_id)]));
    for (const accountId of ids) {
      if (!alive.current) return;
      await examine(accountId, proxied.get(accountId) ?? false);
    }
  };

  useEffect(() => {
    // StrictMode mounts twice in development; the run must start once.
    if (started.current) return;
    started.current = true;
    setRows(Object.fromEntries(accountIds.map((id) => [id, { state: 'pending' as const }])));
    void run(accountIds).catch(() => {
      // The fleet could not be read: nothing was checked, and every row says so.
      setRows(
        Object.fromEntries(
          accountIds.map((id) => [id, { state: 'unchecked' as const, reason: undefined }]),
        ),
      );
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- a one-shot start per mount
  }, []);

  const states = accountIds.map((id) => rows[id]?.state ?? 'pending');
  const busy = states.some((state) => state === 'pending' || state === 'working');
  const held = accountIds.filter((id) => !PROCEEDS.has(rows[id]?.state ?? 'pending'));

  return (
    <>
      <div className="mb-4 type-body text-content-subtle">{t('accounts.addWizard.audit.hint')}</div>
      <div className="flex flex-col gap-3">
        {accountIds.map((accountId) => {
          const row = rows[accountId] ?? { state: 'pending' as const };
          const decide = row.state === 'foreign';
          return (
            <div
              key={accountId}
              className="flex items-center gap-3 rounded-md border border-line bg-surface-card px-3 py-3"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate type-body-medium">{sources[accountId] ?? accountId}</div>
                <div
                  className={`text-small ${decide || row.state === 'unchecked' ? 'text-danger' : 'text-content-subtle'}`}
                >
                  {t(`accounts.addWizard.audit.state.${row.state}`, { count: row.foreign ?? 0 })}
                  {row.reason ? ` — ${row.reason}` : ''}
                </div>
              </div>
              {row.state === 'working' ? <Spinner className="m-1" /> : null}
              {row.state === 'clean' || row.state === 'closed' ? (
                <FeedbackMark result="ok" />
              ) : null}
              {decide ? (
                <div className="flex shrink-0 gap-2">
                  <Button
                    size="sm"
                    variant="danger"
                    disabled={busy}
                    onClick={() => {
                      void closeOne(accountId).finally(onImported);
                    }}
                  >
                    {t('accounts.edit.sessionsClose')}
                  </Button>
                  <Button
                    size="sm"
                    disabled={busy}
                    onClick={() => {
                      setRow(accountId, { state: 'continued' });
                    }}
                  >
                    {t('accounts.addWizard.audit.continue')}
                  </Button>
                </div>
              ) : null}
              {row.state === 'unchecked' ? (
                <div className="flex shrink-0 gap-2">
                  <IconButton
                    tone="primary"
                    aria-label={t('accounts.addWizard.retry')}
                    title={t('accounts.addWizard.retry')}
                    disabled={busy}
                    onClick={() => {
                      void examine(accountId, true);
                    }}
                  >
                    <Icon name="refresh" size={16} />
                  </IconButton>
                  <Button
                    size="sm"
                    disabled={busy}
                    onClick={() => {
                      setRow(accountId, { state: 'continued' });
                    }}
                  >
                    {t('accounts.addWizard.audit.continue')}
                  </Button>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
      {!busy && held.length > 0 ? (
        <div className="mt-4">
          <Notice tone="warning" role="status" className="py-3 text-small font-medium">
            {t('accounts.addWizard.audit.held', { count: held.length })}
          </Notice>
        </div>
      ) : null}
      <div className="mt-6 flex justify-end gap-2">
        <Button
          variant="primary"
          disabled={busy}
          onClick={() => {
            onDone(accountIds.filter((id) => PROCEEDS.has(rows[id]?.state ?? 'pending')));
          }}
        >
          {t('accounts.addWizard.next')}
        </Button>
      </div>
    </>
  );
}
