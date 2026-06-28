import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState, type ChangeEvent } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountsQueryOptions,
  checkAccountMutation,
  deleteAccountMutation,
  importAccountTdataMutation,
} from '@/entities/account';
import { AccountsTable } from '@/widgets/accounts-table';

const PAGE_SIZE = 20;

export function AccountsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);

  const [search, setSearch] = useState('');
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);
  const [busyId, setBusyId] = useState<string | null>(null);

  const cursor = cursorStack[cursorStack.length - 1] ?? undefined;
  const { data, isPending, isError } = useQuery(
    accountsQueryOptions({ query: { query: search, status: 'all', cursor, limit: PAGE_SIZE } }),
  );

  const invalidate = () => {
    void queryClient.invalidateQueries();
  };
  const check = useMutation(checkAccountMutation());
  const remove = useMutation(deleteAccountMutation());
  const importTdata = useMutation(importAccountTdataMutation());

  const onCheck = (accountId: string) => {
    setBusyId(accountId);
    check.mutate(
      { body: { account_id: accountId } },
      {
        onSettled: () => {
          setBusyId(null);
          invalidate();
        },
      },
    );
  };
  const onDelete = (accountId: string) => {
    setBusyId(accountId);
    remove.mutate(
      { path: { account_id: accountId } },
      {
        onSettled: () => {
          setBusyId(null);
          invalidate();
        },
      },
    );
  };
  const onImport = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    importTdata.mutate({ body: { file } }, { onSettled: invalidate });
    event.target.value = '';
  };

  const items = data?.items ?? [];
  const stats: { label: string; value: number; cls: string }[] = [
    { label: t('accounts.stats.total'), value: items.length, cls: 'text-ink' },
    {
      label: t('accounts.stats.alive'),
      value: items.filter((a) => a.status === 'alive').length,
      cls: 'text-success',
    },
    {
      label: t('accounts.stats.attention'),
      value: items.filter((a) => a.status === 'unauthorized' || a.status === 'flood_wait').length,
      cls: 'text-warning',
    },
    {
      label: t('accounts.stats.errors'),
      value: items.filter((a) => a.status.includes('error')).length,
      cls: 'text-danger',
    },
  ];

  const hasPrev = cursorStack.length > 1;
  const hasNext = Boolean(data?.next_cursor);

  return (
    <div className="tb-fadeup">
      <div className="mb-[18px] flex flex-wrap items-center justify-between gap-4">
        <h1 className="m-0 text-[22px] font-bold tracking-[-0.02em]">{t('accounts.title')}</h1>
        <div className="flex items-center gap-2">
          <div className="relative flex items-center">
            <svg
              className="pointer-events-none absolute left-3 text-ink-subtle"
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <circle cx="11" cy="11" r="7" />
              <path d="m21 21-4.3-4.3" />
            </svg>
            <input
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setCursorStack([null]);
              }}
              placeholder={t('accounts.searchPlaceholder')}
              className="tb-time h-[38px] w-[220px] rounded-full border border-line bg-white pl-9 pr-3 text-[13px] outline-none"
            />
          </div>
          <button
            type="button"
            onClick={() => fileInput.current?.click()}
            className="rounded-full bg-primary px-4 py-2 text-[13px] font-medium text-white"
          >
            + {t('accounts.actions.add')}
          </button>
          <input ref={fileInput} type="file" accept=".zip" className="hidden" onChange={onImport} />
        </div>
      </div>

      <div className="mb-4 flex flex-wrap gap-[10px]">
        {stats.map((stat) => (
          <div
            key={stat.label}
            className="min-w-[120px] rounded-xl border border-line bg-white px-4 py-[11px]"
          >
            <div className={`text-[20px] font-bold ${stat.cls}`}>{stat.value}</div>
            <div className="mt-px text-[11px] text-ink-muted">{stat.label}</div>
          </div>
        ))}
      </div>

      {isPending ? (
        <p className="text-ink-muted">{t('accounts.loading')}</p>
      ) : isError ? (
        <p role="alert" className="text-danger">
          {t('accounts.error')}
        </p>
      ) : items.length === 0 ? (
        <div className="rounded-2xl border border-line bg-white px-4 py-16 text-center text-[13px] text-ink-subtle">
          {t('accounts.empty')}
        </div>
      ) : (
        <>
          <AccountsTable data={items} onCheck={onCheck} onDelete={onDelete} busyId={busyId} />
          <div className="mt-4 flex items-center justify-end gap-2">
            <button
              type="button"
              disabled={!hasPrev}
              onClick={() => {
                setCursorStack((stack) => stack.slice(0, -1));
              }}
              className="rounded-full border border-line bg-white px-4 py-[7px] text-[13px] disabled:opacity-50"
            >
              {t('accounts.pagination.prev')}
            </button>
            <button
              type="button"
              disabled={!hasNext}
              onClick={() => {
                setCursorStack((stack) => [...stack, data?.next_cursor ?? null]);
              }}
              className="rounded-full border border-line bg-white px-4 py-[7px] text-[13px] disabled:opacity-50"
            >
              {t('accounts.pagination.next')}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
