import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountsQueryOptions,
  accountStatsQueryOptions,
  checkAccountMutation,
  deleteAccountMutation,
  invalidateAccountViews,
} from '@/entities/account';
import { Button, Card } from '@/shared/ui';

import type { AccountRead } from '@/shared/api';
import { useTransientFeedback } from '@/shared/lib';
import { AccountEdit, AddAccountModal, ProfileModal, ProxyAddModal } from '@/widgets/account-edit';
import { AccountsTable, DeleteAccountModal } from '@/widgets/accounts-table';
import { ProxyPool } from '@/widgets/proxy-pool';

const PAGE_SIZE = 20;
// The generated query key embeds `query`, so an undebounced search box means a
// brand-new key per keystroke — no cached data, `isPending` true, and the table
// plus the pagination row replaced by the loading line on every character.
const SEARCH_DEBOUNCE_MS = 300;

export function AccountsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const [search, setSearch] = useState('');
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);
  // A Set, not one id: check and delete are per row and both can be in flight at
  // once. With a single string the second click moved the spinner off the first
  // row and re-enabled its buttons mid-request, and the first response to land
  // cleared the OTHER row's spinner.
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(new Set());
  const [editingRow, setEditingRow] = useState<AccountRead | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [proxyAdding, setProxyAdding] = useState(false);
  const [profilingRow, setProfilingRow] = useState<AccountRead | null>(null);

  const [debouncedSearch, setDebouncedSearch] = useState('');
  useEffect(() => {
    const id = window.setTimeout(() => {
      setDebouncedSearch(search);
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(id);
    };
  }, [search]);

  const cursor = cursorStack[cursorStack.length - 1] ?? undefined;
  const { data, isPending, isError } = useQuery({
    ...accountsQueryOptions({
      query: { query: debouncedSearch, status: 'all', cursor, limit: PAGE_SIZE },
    }),
    // Keep the last page on screen while the next key loads, so a search or a
    // page turn doesn't blank the table (and unmount an open edit view).
    placeholderData: keepPreviousData,
  });
  // Fleet-wide status roll-up for the tiles — spans the whole table, so the
  // counts stay correct across pagination and search (unlike counting items).
  const { data: fleetStats } = useQuery(accountStatsQueryOptions());

  // Scoped, never the whole cache — the entity owns the key set (check / delete
  // / import all touch exactly those three queries).
  const invalidate = () => {
    invalidateAccountViews(queryClient);
  };
  // Keyed by row for the same reason as `busyIds`: two checks can be in flight,
  // and a single slot would flash the second row's verdict onto the first.
  const { feedback: checkResults, mark: markChecked } = useTransientFeedback();
  const check = useMutation(checkAccountMutation());
  const remove = useMutation(deleteAccountMutation());

  const markBusy = (accountId: string, busy: boolean) => {
    setBusyIds((ids) => {
      const next = new Set(ids);
      if (busy) next.add(accountId);
      else next.delete(accountId);
      return next;
    });
  };
  // mutateAsync, not mutate+onSettled: one useMutation is ONE callback slot, so
  // acting on a second row took the slot over and the first row's invalidate was
  // dropped — its refreshed status never reached the table. A promise per call
  // also captures its own accountId instead of the hook's latest variables.
  // The global mutationCache still toasts the failure; .catch only keeps the
  // rejection from escaping as unhandled.
  const runOnRow = (accountId: string, call: Promise<unknown>) => {
    markBusy(accountId, true);
    void call
      .finally(() => {
        markBusy(accountId, false);
        invalidate();
      })
      .catch(() => undefined);
  };
  // The spinner alone left the operator guessing: a check that answered
  // "unauthorized" looked exactly like one that answered "alive".
  const onCheck = (accountId: string) => {
    const call = check.mutateAsync({ body: { account_id: accountId } });
    // A second subscription to the same promise, not a wrapper: `runOnRow` owns
    // the busy flag and swallows the rejection, and threading the verdict
    // through it would put check-only state in the delete path too.
    void call.then(
      (checked) => {
        markChecked(accountId, checked.status === 'alive');
      },
      () => {
        markChecked(accountId, false);
      },
    );
    runOnRow(accountId, call);
  };
  const onDelete = (accountId: string) => {
    setDeletingId(accountId);
  };
  const confirmDelete = () => {
    if (!deletingId) return;
    runOnRow(deletingId, remove.mutateAsync({ path: { account_id: deletingId } }));
  };
  const items = data?.items ?? [];
  // The design's five stat tiles (accStats): total / active / idle / needs-code /
  // problem, each with its own colour. Values come from the fleet-wide stats
  // query, not the current page, so they hold across pagination and search.
  const stats: { label: string; value: number; cls: string }[] = [
    { label: t('accounts.stats.total'), value: fleetStats?.total ?? 0, cls: 'text-ink' },
    { label: t('accounts.stats.active'), value: fleetStats?.active ?? 0, cls: 'text-success-deep' },
    { label: t('accounts.stats.idle'), value: fleetStats?.idle ?? 0, cls: 'text-warning-deep' },
    { label: t('accounts.stats.code'), value: fleetStats?.needs_code ?? 0, cls: 'text-primary' },
    { label: t('accounts.stats.problem'), value: fleetStats?.problem ?? 0, cls: 'text-danger' },
  ];

  const hasPrev = cursorStack.length > 1;
  const hasNext = Boolean(data?.next_cursor);

  // Derive the edited/profiled row from the live list each render so it
  // reflects the latest refetch (e.g. status flips from 'unauthorized' after a
  // code login), rather than a stale snapshot captured at click time. Both keep
  // the click-time row as a fallback so an open view doesn't vanish when the
  // account drops out of the current filtered page after an invalidate (e.g. a
  // renamed account no longer matches the search). The edit view needs that at
  // least as much as the modal: its 2FA card holds a one-time plaintext password
  // and that card's own success invalidates this list.
  const editing = editingRow
    ? (items.find((a) => a.account_id === editingRow.account_id) ?? editingRow)
    : null;
  const profiling = profilingRow
    ? (items.find((a) => a.account_id === profilingRow.account_id) ?? profilingRow)
    : null;
  if (editing) {
    return (
      <AccountEdit
        account={editing}
        onBack={() => {
          setEditingRow(null);
        }}
      />
    );
  }

  return (
    <div className="tb-fadeup">
      <ProxyPool
        onAdd={() => {
          setProxyAdding(true);
        }}
      />

      <div className="mb-xl flex flex-wrap items-center justify-between gap-lg">
        <h1 className="m-0 type-page-title">{t('accounts.title')}</h1>
        <div className="flex w-full items-center gap-sm sm:w-auto">
          {/* The wrapper grows, not the input: the icon is an absolute sibling. */}
          <div className="relative flex flex-1 items-center sm:flex-none">
            <svg
              className="pointer-events-none absolute left-lg text-ink-subtle"
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
              // eslint-disable-next-line design-tokens/no-raw-values -- see the note in the rule: the search pill's own height, one component's internal layout
              className="tb-time h-[38px] w-full rounded-full border border-line bg-white pl-[36px] pr-md text-lead outline-none sm:w-tip"
            />
          </div>
          <Button
            variant="primary"
            size="sm"
            onClick={() => {
              setAdding(true);
            }}
          >
            + {t('accounts.actions.add')}
          </Button>
        </div>
      </div>

      <div className="mb-lg flex flex-wrap gap-md">
        {stats.map((stat) => (
          <div
            key={stat.label}
            className="min-w-col rounded-lg border border-line bg-white px-lg py-md"
          >
            <div className={`type-stat ${stat.cls}`}>{stat.value}</div>
            <div className="mt-px type-caption">{stat.label}</div>
          </div>
        ))}
      </div>

      {isPending ? (
        <p className="text-ink-muted">{t('accounts.loading')}</p>
      ) : isError ? (
        <p role="alert" className="text-danger">
          {t('accounts.error')}
        </p>
      ) : (
        <>
          {items.length === 0 ? (
            <Card className="px-lg py-empty text-center type-prose">{t('accounts.empty')}</Card>
          ) : (
            <AccountsTable
              data={items}
              onCheck={onCheck}
              onDelete={onDelete}
              onOpen={(account) => {
                setEditingRow(account);
              }}
              onProfile={(account) => {
                setProfilingRow(account);
              }}
              busyIds={busyIds}
              checkResults={checkResults}
            />
          )}
          {/* The pagination row lives outside the empty branch: deleting the
              last row of page 2 empties the list, and with Prev buried in the
              else-branch the only ways back were the search box and a reload.
              A genuinely empty FIRST page still shows the bare empty state. */}
          {items.length > 0 || hasPrev ? (
            <div className="mt-lg flex items-center justify-end gap-sm">
              <button
                type="button"
                disabled={!hasPrev}
                onClick={() => {
                  setCursorStack((stack) => stack.slice(0, -1));
                }}
                className="rounded-full border border-line bg-white px-lg py-sm text-lead disabled:opacity-50"
              >
                {t('accounts.pagination.prev')}
              </button>
              <button
                type="button"
                disabled={!hasNext}
                onClick={() => {
                  setCursorStack((stack) => [...stack, data?.next_cursor ?? null]);
                }}
                className="rounded-full border border-line bg-white px-lg py-sm text-lead disabled:opacity-50"
              >
                {t('accounts.pagination.next')}
              </button>
            </div>
          ) : null}
        </>
      )}
      {deletingId ? (
        <DeleteAccountModal
          phone={items.find((a) => a.account_id === deletingId)?.phone ?? deletingId}
          onClose={() => {
            setDeletingId(null);
          }}
          onConfirm={confirmDelete}
        />
      ) : null}
      {adding ? (
        <AddAccountModal
          onClose={() => {
            setAdding(false);
          }}
          onImported={invalidate}
        />
      ) : null}
      {proxyAdding ? (
        <ProxyAddModal
          onClose={() => {
            setProxyAdding(false);
          }}
        />
      ) : null}
      {profiling ? (
        <ProfileModal
          account={profiling}
          onClose={() => {
            setProfilingRow(null);
          }}
        />
      ) : null}
    </div>
  );
}
