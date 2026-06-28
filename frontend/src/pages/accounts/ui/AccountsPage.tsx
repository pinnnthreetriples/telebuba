import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState, type ChangeEvent } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountsQueryOptions,
  checkAccountMutation,
  deleteAccountMutation,
  importAccountTdataMutation,
} from '@/entities/account';
import type { AccountRead } from '@/shared/api';
import { AccountEdit } from '@/widgets/account-edit';
import { AccountsTable } from '@/widgets/accounts-table';

const PAGE_SIZE = 20;

// ponytail: mock pool until a proxy-pool endpoint exists — design-first, data later.
const PROXY_POOL = [
  { host: 'nl-1.proxyhub.net', port: 1080, type: 'SOCKS5', cc: 'nl', used: 3, cap: 3 },
  { host: 'de-2.proxyhub.net', port: 1080, type: 'SOCKS5', cc: 'de', used: 2, cap: 3 },
  { host: 'us-3.proxyhub.net', port: 8080, type: 'HTTPS', cc: 'us', used: 1, cap: 3 },
] as const;

// The design's proxy-pool card: one card per proxy with a usage bar (N/3).
function ProxyPool() {
  const { t } = useTranslation();
  return (
    <div className="mb-4 rounded-2xl border border-line bg-white px-[18px] py-4">
      <div className="mb-[13px] flex flex-wrap items-center justify-between gap-3">
        <div>
          <span className="text-[14px] font-semibold">{t('accounts.proxyPool.title')}</span>
          <span className="ml-2 text-[12px] text-ink-subtle">
            {t('accounts.proxyPool.subtitle')}
          </span>
        </div>
        <button
          type="button"
          className="inline-flex items-center gap-[6px] rounded-full bg-primary px-[15px] py-[7px] text-[12.5px] font-medium text-white"
        >
          <svg
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
          >
            <path d="M12 5v14M5 12h14" />
          </svg>
          {t('accounts.proxyPool.add')}
        </button>
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(232px,1fr))] gap-[10px]">
        {PROXY_POOL.map((proxy) => {
          const full = proxy.used >= proxy.cap;
          const free = proxy.cap - proxy.used;
          const pct = Math.round((proxy.used / proxy.cap) * 100);
          return (
            <div
              key={`${proxy.host}:${String(proxy.port)}`}
              className={`rounded-xl border p-3 ${full ? 'border-danger/30 bg-danger-tint' : 'border-line bg-white'}`}
            >
              <div className="flex items-center gap-[9px]">
                <span className={`fi fi-${proxy.cc} h-4 w-[22px] shrink-0 rounded-[3px]`} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12.5px] font-semibold">
                    {proxy.host}:{proxy.port}
                  </div>
                  <div className="mt-px text-[11px] text-ink-subtle">{proxy.type}</div>
                </div>
                <button
                  type="button"
                  aria-label={t('accounts.actions.delete')}
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[#b6b4af]"
                >
                  <svg
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <path d="M18 6 6 18M6 6l12 12" />
                  </svg>
                </button>
              </div>
              <div className="mt-3">
                <div className="mb-[5px] flex items-center justify-between">
                  <span className="text-[11px] text-ink-muted">
                    {t('accounts.proxyPool.accounts')}
                  </span>
                  <span
                    className={`text-[11.5px] font-semibold ${full ? 'text-danger' : 'text-primary'}`}
                  >
                    {proxy.used} / {proxy.cap}
                  </span>
                </div>
                <div className="h-[5px] overflow-hidden rounded-full bg-track">
                  <div
                    className={`h-full rounded-full ${full ? 'bg-danger' : 'bg-primary'}`}
                    style={{ width: `${String(pct)}%` }}
                  />
                </div>
                <div
                  className={`mt-[5px] text-[10.5px] ${full ? 'text-danger' : 'text-ink-subtle'}`}
                >
                  {full
                    ? t('accounts.proxyPool.full')
                    : t('accounts.proxyPool.free', { count: free })}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function AccountsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);

  const [search, setSearch] = useState('');
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [editing, setEditing] = useState<AccountRead | null>(null);

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

  if (editing) {
    return (
      <AccountEdit
        account={editing}
        onBack={() => {
          setEditing(null);
        }}
      />
    );
  }

  return (
    <div className="tb-fadeup">
      <ProxyPool />

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
          <AccountsTable
            data={items}
            onCheck={onCheck}
            onDelete={onDelete}
            onOpen={setEditing}
            busyId={busyId}
          />
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
