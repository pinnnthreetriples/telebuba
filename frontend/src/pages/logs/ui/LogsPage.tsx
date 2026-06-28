import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { LogStatusBadge, logsQueryOptions } from '@/entities/log';
import type { PageLogEntry } from '@/shared/api';
import { useLogEventStream } from '@/shared/lib';

const PAGE_SIZE = 50;
const STATUS_FILTERS = ['all', 'success', 'warning', 'error'] as const;
type StatusFilter = (typeof STATUS_FILTERS)[number];

const TH =
  'px-4 py-[11px] text-left text-[11px] font-medium uppercase tracking-[0.04em] text-ink-subtle';

export function LogsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<StatusFilter>('all');
  const [account, setAccount] = useState('');
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);

  const cursor = cursorStack[cursorStack.length - 1] ?? undefined;
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
    <div className="tb-fadeup">
      <h1 className="m-0 mb-[18px] text-[22px] font-bold tracking-[-0.02em]">{t('logs.title')}</h1>

      <div className="mb-[14px] flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-full bg-white p-[3px]">
          {STATUS_FILTERS.map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => {
                setStatus(value);
                resetPaging();
              }}
              className={`rounded-full px-[14px] py-[6px] text-[12px] font-medium transition-colors ${status === value ? 'bg-primary text-white' : 'text-ink-muted'}`}
            >
              {t(`logs.filter.${value}`)}
            </button>
          ))}
        </div>
        <div className="flex-1" />
        <input
          type="search"
          value={account}
          onChange={(event) => {
            setAccount(event.target.value);
            resetPaging();
          }}
          placeholder={t('logs.filter.account')}
          aria-label={t('logs.filter.account')}
          className="tb-time w-[200px] rounded-full border border-line bg-white px-4 py-[7px] text-[13px] outline-none"
        />
      </div>

      {isPending ? (
        <p className="text-ink-muted">{t('logs.loading')}</p>
      ) : isError ? (
        <p role="alert" className="text-danger">
          {t('logs.error')}
        </p>
      ) : items.length === 0 ? (
        <div className="rounded-2xl border border-line bg-white px-4 py-16 text-center text-[13px] text-ink-subtle">
          {t('logs.empty')}
        </div>
      ) : (
        <>
          <div className="overflow-hidden rounded-2xl border border-line bg-white">
            <div className="tb-scroll overflow-x-auto">
              <table className="w-full min-w-[760px] border-collapse">
                <thead>
                  <tr className="bg-surface">
                    <th className={`${TH} w-[120px]`}>{t('logs.col.time')}</th>
                    <th className={`${TH} w-[110px]`}>{t('logs.col.level')}</th>
                    <th className={`${TH} w-[150px]`}>{t('logs.col.account')}</th>
                    <th className={TH}>{t('logs.col.event')}</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((row) => (
                    <tr key={row.id} className="border-t border-[#f0eeeb]">
                      <td className="px-4 py-[10px] font-mono text-[12px] text-ink-subtle">
                        {row.created_at}
                      </td>
                      <td className="px-4 py-[10px]">
                        <LogStatusBadge status={row.status} />
                      </td>
                      <td className="px-4 py-[10px] text-[12.5px] text-[#3a3a3a]">
                        {row.account_id ?? '—'}
                      </td>
                      <td className="px-4 py-[10px] text-[12.5px] text-[#3a3a3a]">{row.event}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div className="mt-4 flex items-center justify-end gap-2">
            <button
              type="button"
              disabled={!hasPrev}
              onClick={() => {
                setCursorStack((stack) => stack.slice(0, -1));
              }}
              className="rounded-full border border-line bg-white px-4 py-[7px] text-[13px] disabled:opacity-50"
            >
              {t('logs.pagination.prev')}
            </button>
            <button
              type="button"
              disabled={!hasNext}
              onClick={() => {
                setCursorStack((stack) => [...stack, data.next_cursor ?? null]);
              }}
              className="rounded-full border border-line bg-white px-4 py-[7px] text-[13px] disabled:opacity-50"
            >
              {t('logs.pagination.next')}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
