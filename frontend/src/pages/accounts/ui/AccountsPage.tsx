import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { RowSelectionState, SortingState } from '@tanstack/react-table';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountsQueryOptions,
  checkAccountMutation,
  deleteAccountMutation,
  importAccountTdataMutation,
} from '@/entities/account';
import { AccountsTable } from '@/widgets/accounts-table';

const PAGE_SIZE = 20;
const STATUS_FILTERS = ['all', 'alive', 'new', 'unauthorized', 'flood_wait'] as const;

export function AccountsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<string>('all');
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);
  const [sorting, setSorting] = useState<SortingState>([]);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [busyId, setBusyId] = useState<string | null>(null);

  const cursor = cursorStack[cursorStack.length - 1] ?? undefined;
  const { data, isPending, isError } = useQuery(
    accountsQueryOptions({ query: { query: search, status, cursor, limit: PAGE_SIZE } }),
  );

  const invalidate = () => {
    void queryClient.invalidateQueries();
  };
  const check = useMutation(checkAccountMutation());
  const remove = useMutation(deleteAccountMutation());
  const importTdata = useMutation(importAccountTdataMutation());

  const resetToFirstPage = () => {
    setCursorStack([null]);
  };

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

  const onImport = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    importTdata.mutate({ body: { file } }, { onSettled: invalidate });
    event.target.value = '';
  };

  const hasPrev = cursorStack.length > 1;
  const hasNext = Boolean(data?.next_cursor);

  return (
    <main className="mx-auto max-w-5xl p-8">
      <header className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">{t('accounts.title')}</h1>
        <label className="cursor-pointer rounded-md bg-primary px-3 py-2 text-sm font-medium text-white hover:opacity-90">
          {t('accounts.actions.importTdata')}
          <input type="file" accept=".zip" className="hidden" onChange={onImport} />
        </label>
      </header>

      <div className="mb-4 flex gap-3">
        <input
          type="search"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            resetToFirstPage();
          }}
          placeholder={t('accounts.searchPlaceholder')}
          className="flex-1 rounded-md border border-line bg-surface px-3 py-2 text-sm"
        />
        <select
          value={status}
          onChange={(event) => {
            setStatus(event.target.value);
            resetToFirstPage();
          }}
          className="rounded-md border border-line bg-surface px-3 py-2 text-sm"
        >
          {STATUS_FILTERS.map((value) => (
            <option key={value} value={value}>
              {t(`accounts.filter.${value}`)}
            </option>
          ))}
        </select>
      </div>

      {isPending ? (
        <p className="text-ink-muted">{t('accounts.loading')}</p>
      ) : isError ? (
        <p role="alert" className="text-danger">
          {t('accounts.error')}
        </p>
      ) : data.items.length === 0 ? (
        <p className="text-ink-subtle">{t('accounts.empty')}</p>
      ) : (
        <>
          <AccountsTable
            data={data.items}
            sorting={sorting}
            onSortingChange={setSorting}
            rowSelection={rowSelection}
            onRowSelectionChange={setRowSelection}
            onCheck={onCheck}
            onDelete={onDelete}
            busyId={busyId}
          />
          <div className="mt-4 flex items-center justify-end gap-2">
            <button
              type="button"
              className="rounded border border-line px-3 py-1 text-sm disabled:opacity-50"
              disabled={!hasPrev}
              onClick={() => {
                setCursorStack((stack) => stack.slice(0, -1));
              }}
            >
              {t('accounts.pagination.prev')}
            </button>
            <button
              type="button"
              className="rounded border border-line px-3 py-1 text-sm disabled:opacity-50"
              disabled={!hasNext}
              onClick={() => {
                setCursorStack((stack) => [...stack, data.next_cursor ?? null]);
              }}
            >
              {t('accounts.pagination.next')}
            </button>
          </div>
        </>
      )}
    </main>
  );
}
