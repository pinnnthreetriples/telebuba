import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { LogStatusBadge, logsQueryOptions } from '@/entities/log';
import type { PageLogEntry } from '@/shared/api';
import { useLogEventStream } from '@/shared/lib';

const PAGE_SIZE = 50;
const STATUS_FILTERS = ['all', 'success', 'warning', 'error'] as const;
type StatusFilter = (typeof STATUS_FILTERS)[number];

export function LogsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<StatusFilter>('all');
  const [account, setAccount] = useState('');
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);

  const cursor = cursorStack[cursorStack.length - 1] ?? undefined;
  // Live via SSE (setQueryData below) — no refetch poll; pagination still uses
  // the query for the older pages.
  const { data, isPending, isError } = useQuery(
    logsQueryOptions({
      query: { status, account_id: account, cursor, limit: PAGE_SIZE },
    }),
  );

  const items = data?.items ?? [];
  const hasPrev = cursorStack.length > 1;
  const hasNext = Boolean(data?.next_cursor);

  // Live tail: prepend each incoming row to the newest page's cache, in place,
  // when it matches the active filter (no refetch).
  useLogEventStream((entry) => {
    if (hasPrev) return;
    if (status !== 'all' && entry.status !== status) return;
    if (account && entry.account_id !== account) return;
    const { queryKey } = logsQueryOptions({
      query: { status, account_id: account, cursor: undefined, limit: PAGE_SIZE },
    });
    queryClient.setQueryData<PageLogEntry>(queryKey, (old) => {
      if (!old) return old;
      if (old.items.some((row) => row.id === entry.id)) return old;
      return { ...old, items: [entry, ...old.items].slice(0, PAGE_SIZE) };
    });
  });

  const resetPaging = () => {
    setCursorStack([null]);
  };

  return (
    <main className="mx-auto max-w-5xl space-y-4 p-8">
      <h1 className="text-2xl font-semibold">{t('logs.title')}</h1>

      <div className="flex gap-3">
        <select
          value={status}
          onChange={(event) => {
            setStatus(event.target.value as StatusFilter);
            resetPaging();
          }}
          aria-label={t('logs.filter.status')}
          className="rounded-md border border-line bg-surface px-3 py-2 text-sm"
        >
          {STATUS_FILTERS.map((value) => (
            <option key={value} value={value}>
              {t(`logs.filter.${value}`)}
            </option>
          ))}
        </select>
        <input
          type="search"
          value={account}
          onChange={(event) => {
            setAccount(event.target.value);
            resetPaging();
          }}
          placeholder={t('logs.filter.account')}
          aria-label={t('logs.filter.account')}
          className="flex-1 rounded-md border border-line bg-surface px-3 py-2 text-sm"
        />
      </div>

      {isPending ? (
        <p className="text-ink-muted">{t('logs.loading')}</p>
      ) : isError ? (
        <p role="alert" className="text-danger">
          {t('logs.error')}
        </p>
      ) : items.length === 0 ? (
        <p className="text-ink-subtle">{t('logs.empty')}</p>
      ) : (
        <>
          <div className="overflow-x-auto rounded-md border border-line bg-surface">
            <table className="w-full border-collapse text-left text-sm">
              <thead>
                <tr className="border-b border-line text-xs text-ink-muted">
                  <th className="px-4 py-2">{t('logs.col.time')}</th>
                  <th className="px-4 py-2">{t('logs.col.status')}</th>
                  <th className="px-4 py-2">{t('logs.col.account')}</th>
                  <th className="px-4 py-2">{t('logs.col.event')}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr key={row.id} className="border-b border-line last:border-0">
                    <td className="px-4 py-2 font-mono text-xs text-ink-muted">{row.created_at}</td>
                    <td className="px-4 py-2">
                      <LogStatusBadge status={row.status} />
                    </td>
                    <td className="px-4 py-2 font-mono text-xs">{row.account_id ?? '—'}</td>
                    <td className="px-4 py-2 font-mono text-xs">{row.event}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-end gap-2">
            <button
              type="button"
              className="rounded border border-line px-3 py-1 text-sm disabled:opacity-50"
              disabled={!hasPrev}
              onClick={() => {
                setCursorStack((stack) => stack.slice(0, -1));
              }}
            >
              {t('logs.pagination.prev')}
            </button>
            <button
              type="button"
              className="rounded border border-line px-3 py-1 text-sm disabled:opacity-50"
              disabled={!hasNext}
              onClick={() => {
                setCursorStack((stack) => [...stack, data.next_cursor ?? null]);
              }}
            >
              {t('logs.pagination.next')}
            </button>
          </div>
        </>
      )}
    </main>
  );
}
